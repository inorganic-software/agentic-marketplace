# Mejoras de los evals

Plan de mejoras para `evals/`, sacado de comparar el framework con cómo se evalúan hoy los skills, los agentes y los harnesses (Anthropic, OpenAI, benchmarks públicos y práctica de la comunidad). Pensado para ir atacándolo en varias sesiones: cada mejora tiene un ID, el problema, la propuesta, los ficheros afectados y cuándo está hecha. Marca la casilla al cerrarla y anota el commit o PR.

Estado de partida (sesión `20261006-133002`, Claude Code, `claude-sonnet-5-5` como agente y `claude-opus-5-5` como juez): 7 casos de `commons/git-workflow`, 20 tests, 20 pasan con 3/3 runs. La suite está saturada y no discrimina.

## Resumen

| ID | Mejora | Prioridad | Esfuerzo | Estado |
|---|---|---|---|---|
| M1 | Checks deterministas en el golden | Alta | Bajo | ☑ (sin commitear) |
| M2 | pass^k (N de N) para las prohibiciones | Alta | Bajo | ☑ (sin commitear) |
| M3 | Baseline sin el skill y delta | Alta | Medio | ☑ (sin commitear) |
| M4 | Evals de activación con negativos | Alta | Medio | ☑ (sin commitear) |
| M5 | Casos informativos (que no bloquean) | Alta | Bajo | ☑ (sin commitear) |
| M6 | Juez de otra familia y fijo entre proveedores | – | – | ✗ descartada |
| M7 | Ajustes del prompt y del schema del juez | – | – | ✗ descartada |
| M8 | Criterios obligatorios en `outcome` | Media | Bajo | ☑ (sin commitear) |
| M9 | Calibrar el juez con etiquetas humanas | – | – | ✗ descartada |
| M10 | Estadística: intervalos, pass^k, comparaciones emparejadas | – | – | ✗ descartada |
| M11 | Detectar checks que no discriminan y casos inestables | – | – | ✗ descartada |
| M12 | Variantes de prompt | Baja | Medio | ☑ (sin commitear) |
| M13 | Eficiencia como métrica | Baja | Bajo | ☑ (sin commitear) |
| M14 | Contenedor por run | – | – | ✗ descartada |
| M15 | Casos desde fallos reales | – | – | ✗ descartada |
| M16 | Evaluar el harness completo | Baja | Alto | ☐ |
| M17 | Probar `claude plugin eval` en paralelo | Baja | Bajo | ☐ |

Orden sugerido: M1 → M2 → M3 → M4 → M5 → M8 → el resto según haga falta. M1 y M2 son poco código y son lo que más fiabilidad añade; M3 a M5 hacen que la suite vuelva a medir algo.

---

## Alta

### M1. Checks deterministas en el golden

**Problema.** Solo `tool_correctness` es determinista. Las prohibiciones (`rules`) y casi todo `expected_outcome` lo juzga un LLM, aunque muchas cosas son exactas y se pueden comprobar con git: nombre de rama, formato del commit, working tree limpio, marcadores de conflicto, que `main` no se reescribió, rebase a medias. El juez añade su variabilidad y su coste a cosas que no lo necesitan.

**Lo que dicen las fuentes.** Las graduaciones con código van primero; el LLM solo donde el código no llega (Anthropic, *Demystifying evals*; OpenAI, *Testing Agent Skills Systematically with Evals*; Hamel Husain). Los benchmarks puntúan el estado final con tests.

**Propuesta.** Un campo nuevo en el golden, `checks`, con comandos de shell que se ejecutan en el workspace después del agente y pasan si salen con código 0. Una métrica nueva, `checks`, determinista, que también cubre las trampas (se evalúa aunque el agente no cambie nada, como `rules`). Mantiene la regla de que un skill nuevo solo necesita cambios en el golden.

```yaml
checks:
  - name: "origin's main was not rewritten"
    run: git --git-dir="$EVAL_SANDBOX/origin.git" merge-base --is-ancestor "$(cat "$EVAL_SANDBOX/main-before")" main
cases:
  - id: commit-feature
    checks:
      - name: working tree is clean
        run: test -z "$(git status --porcelain)"
      - name: branch follows Conventional Branch
        run: git branch --show-current | grep -Eq '^(feat|feature)/[a-z0-9]+(-[a-z0-9]+)*$'
```

Los checks globales (al nivel del golden) son prohibiciones: deben pasar en todos los casos. Los checks de cada caso forman parte del resultado. Si un check necesita el estado de antes, el `setup.sh` de la fixture lo guarda en `$EVAL_SANDBOX`.

**Ficheros.** `evals/src/marketplace_evals/goldens.py` (campo y validación), `metrics/checks.py` (nuevo), `metrics/__init__.py`, `runtimes/runner.py` y `sandbox.py` (ejecutar los checks antes de borrar el sandbox y guardar el resultado en la `Trace`), `trace.py`, tests unitarios, `evals/README.md`, y `golden.yaml` de `git-workflow` (pasar a `checks` lo que sea exacto y dejar en `rules`/`expected_outcome` solo lo que necesita juicio: si la descripción es imperativa, si el conflicto se resolvió con intención, si el scope tiene sentido).

**Hecho cuando.** `git-workflow` tiene sus prohibiciones y sus criterios exactos como `checks`, la métrica sale en el informe, en `results.json` y en `evals-compare`, y hay tests unitarios de la métrica.

**Hecho** (sesión `20261007-141623`, pendiente de commit). Cómo quedó, respecto a la propuesta:
- Dos métricas en vez de una: `rules_always_met` (campo a nivel de golden: las prohibiciones, escritas como lo que debe cumplirse siempre; se ejecutan aunque el agente no cambie nada) y `outcome_checks` (campo `outcome_checks` por caso, fallan sin ejecutarse si el agente no cambió nada). Tienen que pasar todos los checks. M2 solo tiene que marcar `rules_always_met` como estricta. Las `rules` juzgadas pasaron a llamarse `rules_always_met_judged`, para formar pareja, y el loader rechaza claves desconocidas en el golden (un campo renombrado o mal escrito ya no desactiva checks en silencio).
- El estado de antes no lo guarda el `setup.sh`: lo guarda un campo nuevo, `before:`, a nivel de golden, que se ejecuta después del setup y antes del agente.
- Los checks y `before` se ejecutan con `bash -e -o pipefail -c` (sin `-e`, en un check de varias líneas solo contaría la última). Timeout de 60 s. Los checks que fallan salen con su código y su salida en el informe y en `.state.txt`.
- El stub de `gh` guarda el título de `pr create` (`$EVAL_SANDBOX/gh/created-title`) y los SHA de `pr merge` (`$EVAL_SANDBOX/gh/merged`).
- `outcome` con un solo criterio ya no pasa siempre: `max_misses = min(1, len - 1)`.
- En `git-workflow` las `rules` pasaron a `rules_always_met` y el juez solo valora "descripción imperativa" y "conflicto resuelto con intención". Resultado: 24/24 con 3/3; el juez pasó de 39 llamadas a 12 (de 0,78 $ a 0,21 $).

### M2. pass^k para las prohibiciones

**Problema.** Todas las métricas pasan un caso con `--min-passes` de `--runs` (2 de 3 en CI). Con eso, si el agente hace push a `main` en 1 de cada 3 runs, las reglas (`rules_always_met` y `rules_always_met_judged`) y el caso trampa pasan.

**Lo que dicen las fuentes.** pass^k (que las k ejecuciones salgan bien) es la métrica para lo que el usuario necesita que se cumpla siempre (τ-bench; Anthropic). pass@k solo cuando basta con un acierto.

**Propuesta.** Las prohibiciones (`rules_always_met`, `rules_always_met_judged` y los `forbidden_calls` de `tool_correctness`) exigen N de N. Los criterios de calidad siguen con `--min-passes`. Lo más sencillo: un atributo en `MetricSpec` (`strict: bool`) que `CaseResult.passed` respeta. Separar `forbidden_calls` en su propia métrica o tratarlos como estrictos dentro de `tool_correctness`.

**Ficheros.** `metrics/__init__.py`, `evaluation.py`, `reporting/*` (que el informe diga "N/N requerido"), `evals/README.md`.

**Hecho cuando.** Un único run que incumpla una prohibición hace fallar el caso, y lo cubre un test unitario.

**Hecho** (sesión `20261007`, pendiente de commit). Cómo quedó, respecto a la propuesta:
- `MetricSpec` tiene `strict: bool`, fijo en el código (ni flag de CLI ni campo en el golden: lo que dice "este criterio de calidad no puede fallar" es M8). Son estrictas `forbidden_calls`, `rules_always_met` y `rules_always_met_judged`; `expected_calls`, `outcome_checks` y `outcome` siguen con `--min-passes`.
- `tool_correctness` se separó en dos métricas con el nombre de su campo del golden: `expected_calls` (no estricta) y `forbidden_calls` (estricta). Cada una solo se aplica a los casos que tienen ese campo. `metrics/tool_correctness.py` pasó a ser `metrics/calls.py`.
- `CaseResult` guarda `required` (todos los runs si es estricta, `min_passes` si no) y `strict`. Un run con error (runtime, modelo distinto, juez que falla dos veces) cuenta como no aprobado, así que hace fallar una métrica estricta.
- `results.json` pasa al schema 2: cada métrica guarda `required` y `strict` en vez de `min_passes`. El informe Markdown dice "2 of 3 runs must pass, all 3 for prohibitions (strict)" y cada celda "✅ 3/3 (3 req.)"; el terminal, "(strict: all 3)"; `evals-compare` muestra "(N req.)". Con sesiones anteriores, el informe y `evals-compare` usan el `min_passes` de su configuración.
- Las filas `expected_calls` y `forbidden_calls` empiezan sin historial en `evals-compare`: las sesiones anteriores tienen `tool_correctness`, que mezclaba las dos.

### M3. Baseline sin el skill y delta

**Problema.** No sabemos qué aporta el skill. Si el agente ya hace bien un caso sin el skill, el caso no mide el skill.

**Lo que dicen las fuentes.** Es la métrica principal para skills: con skill menos sin skill (Anthropic, *Agent Skills best practices*: "establish a baseline" antes de escribir el skill; skill-creator; `claude plugin eval`, que añade las columnas WITH, W/OUT y Δ). SkillsBench: los skills mejoran +16 puntos de media, pero empeoran 16 de 84 tareas. Si el modelo base aprueba sin el skill, el skill sobra.

**Propuesta.**
- Una opción `--baseline none|<ref de git>`: un segundo grupo de runs por caso sin el plugin (`--plugin-dir` a un plugin vacío) o con la versión del skill en otra ref (`git worktree` o `git show` a una carpeta temporal).
- Las comprobaciones que solo pueden pasar con el skill (`load_skill`, leer el SKILL.md) no cuentan en ninguno de los dos grupos al calcular el delta.
- El informe, `results.json` y `evals-compare` muestran con skill, sin skill y delta por caso y por métrica.
- El delta es informativo; no cambia si el caso pasa o falla.

**Ficheros.** `config.py`, `evaluation.py`, `session.py`, `runtimes/runner.py`, `reporting/*`, `tests/conftest.py` (opción de pytest), `evals/README.md`, `.github/workflows/evals.yml` (decidir si CI ejecuta el baseline siempre o solo bajo demanda, por coste).

**Hecho cuando.** `uv run pytest -m eval -s --baseline -k git-workflow` saca el delta de cada caso, y la sesión resultante se puede comparar en `evals-compare`.

**Plan acordado** (sesión `20261007`, antes de implementar). Cambia la propuesta en esto:
- **Qué se quita.** Solo el skill evaluado, no el plugin: el sandbox ya copia el plugin, y en los runs del baseline se borra `skills/<skill>/` de esa copia antes de lanzar el agente, con el mismo `--plugin-dir`. Los demás skills, MCP y hooks del plugin siguen cargados, como los tendría un usuario (y compiten en la activación). Es más fino que `claude plugin eval --ablation`, que quita el plugin entero.
- **Opción.** `--baseline`, sin valor. Comparar contra otra ref de git queda fuera; si hace falta, será otra opción (`--baseline-ref <ref>`).
- **Runs.** Los mismos que `--runs`, lanzados con los del skill. Cada run del baseline deja sus logs en `.runs/<sesión>/` con un nombre que lo distinga (p. ej. `<caso>-baseline-<i>`).
- **With-only, automático por matcher.** Un matcher de llamadas es with-only si es `load_skill` con el nombre del skill evaluado o `read_file` dentro de su carpeta en `@plugins/` (un `any_of` lo es si alguna alternativa lo es). Nada cambia en el golden.
- **Delta por métrica.** En tasa de runs que pasan: con el skill 3/3, sin el skill 1/3, Δ +67 pp. Para el delta, los dos grupos se puntúan quitando los matchers with-only; si la métrica se queda vacía (`expected_calls` en `git-workflow`), su baseline y su delta salen "–". El resultado oficial con el skill no cambia: el baseline nunca hace fallar un test. El detalle por check también se guarda, para `evals-compare`.
- **Resumen por caso.** Si el caso pasa con el skill y si pasa sin él, con las mismas reglas (`min_passes`, estrictas N de N) y sin lo with-only. Si también pasa sin el skill, aviso informativo "⚠ pasa sin el skill: no lo mide" en terminal, Markdown y `evals-compare` (adelanta parte de M11).
- **pytest.** Dentro de `test_metric`: puntúa también los runs sin el skill y añade "sin skill 1/3 · Δ +67 pp" a su informe. Los ids de los tests no cambian.
- **`results.json`.** Schema 3: con `--baseline`, cada métrica lleva un bloque `baseline` (`passes`, `runs`, `passed`, `checks`, `per_run`) y su `delta`; cada caso, `passed_with` / `passed_without`; la configuración, `baseline: true`. Sin `--baseline`, igual que el schema 2.
- **`evals-compare`.** Dentro de cada celda: "3/3 · sin 1/3 · Δ +67". Las sesiones sin baseline se ven como hoy.
- **CI.** Bajo demanda: solo con la etiqueta `evals:baseline` en el PR (o `workflow_dispatch`). Sin ella, el coste no cambia.

**Ficheros.** `config.py` (`baseline: bool`), `tests/conftest.py` (opción), `sandbox.py` (quitar el skill de la copia), `runtimes/runner.py` (`AgentTask` sabe si es baseline y qué skill quitar), `evaluation.py` (grupo sin skill y puntuación sin lo with-only), `matchers.py` o `metrics/calls.py` (detectar lo with-only), `session.py`, `tests/evals/test_evals.py`, `reporting/{results,terminal,markdown,compare}.py` y `compare.html`, `.github/workflows/evals.yml` (etiqueta), `evals/README.md`, y tests unitarios de la detección with-only, del sandbox sin el skill, del delta y del schema 3.

**Hecho** (sesión `20261007`, pendiente de commit). Cómo quedó, respecto al plan:
- Solo las métricas de llamadas (`expected_calls`, `forbidden_calls`, marcadas `on_calls` en `MetricSpec`) se puntúan sin lo with-only; las demás reutilizan su resultado con el skill, y el juez no se llama dos veces (24 llamadas en vez de 12, no 36). La primera versión quitaba lo with-only del caso entero y volvía a juzgar `outcome`.
- La detección with-only está en `GoldenCase.comparable()` / `skill_only()`: `load_skill` con un `name` que encaje con el skill evaluado, o `read_file` con un `path` que empiece por la carpeta del skill. `load_skill` sin nombre, otro skill u otro fichero del plugin no cuentan.
- `results.json` (schema 3): el bloque `baseline` de cada métrica es `{with_skill, without_skill, delta}` (cada lado, como una métrica: `passes`, `runs`, `checks`, `per_run`...), o `null` si solo el skill puede pasarla; cada caso lleva `baseline: {with_skill, without_skill}`; la configuración, `baseline`.
- El Markdown añade la columna "Without skill" y "· w/o 1/3 · Δ +67 pp" en cada celda. `evals-compare` añade la fila "without skill" en cada golden, "w/o … · Δ …" en métricas y checks, y la fila de configuración "baseline (without skill)". La página no se ha revisado a mano en el navegador; su JS pasa `node --check`.
- CI: etiqueta `evals:baseline` (añadirla lanza un run; otras etiquetas no) y `workflow_dispatch` con la casilla `baseline` (sin comentario en el PR).
- Primera medición (sesión `20261007-175002`, Claude Code, `claude-sonnet-5-5`, 3 runs): 6 de 7 casos fallan sin el skill; el agente sin el skill commitea en `main` (`rules_always_met` +100 pp en los casos de commit y en la trampa) y no sigue las convenciones (`outcome_checks` +100 pp). Dos señales para M5 y M11: `rebase-onto-main-with-conflict` pasa igual sin el skill (no lo mide), y `outcome` ("descripción imperativa", "conflicto resuelto con intención") da Δ 0 en todos los casos. Coste: 2,39 $ frente a ~1,2 $ sin baseline.

### M4. Evals de activación con negativos

**Problema.** Todos los casos son positivos: prompts en los que el skill debe cargarse, y solo se comprueba (con `expected_calls`) que se carga. Nunca se comprueba que no se cargue cuando no toca. Una `description` tan amplia que cargue el skill con cualquier cosa de git pasaría todos los evals. Y los positivos solo cubren la forma concreta de pedir de cada caso.

**Lo que dicen las fuentes.** El `skill-creator` de Anthropic optimiza la `description` con un conjunto de consultas que deben activarla y otras que no, y los mejores negativos son los casi aciertos (cerca del dominio, sin pedir lo que hace el skill). OpenAI, *Testing Agent Skills Systematically with Evals*: medir la activación aparte del comportamiento.

**Hecho** (sesión `20261007`, pendiente de commit). Cómo quedó:
- Una sección nueva en el golden, `skill_loading` (no `activation`: dice qué se mira), con `fixture`, `should_load` y `should_not_load`, cada una una lista de prompts. Cada prompt es un caso propio, `should-load-<slug>` o `should-not-load-<slug>`, con el id sacado del texto (cambiar el texto es una fila nueva en `evals-compare`). `-k should-` ejecuta solo estos.
- Una métrica nueva, `skill_loading`, determinista, que solo se aplica a esos prompts: pasa si el agente carga el skill evaluado (`load_skill` con su nombre o `read_file` de su `SKILL.md`, denegado también) cuando debe, y si no lo carga cuando no debe. No es estricta: `--min-passes`, como un criterio de calidad. Cargar el skill sin que haga falta no rompe nada por sí mismo.
- Los prompts corren con herramientas de solo lectura (`read`, `search` y cargar skills), sin `inspect`, `before`, reglas ni checks: runs cortos, que no pueden hacer el trabajo.
- Con `--baseline`, estos prompts no se corren sin el skill: sin él no puede cargarse.
- Resumen por skill: prompts que pasan de cada tipo y runs que pasan. Sale en la sección `eval skill loading` del terminal y de `summary.txt`, en `results.json` (schema 4: cada prompt lleva `should_load` y hay un bloque `skill_loading` por skill) y en una tabla del comentario del PR. `evals-compare` muestra cada prompt como una fila más, pero no el resumen.
- `git-workflow`: 5 prompts `should_load` (otras formas de pedir commit, nombre de rama, mensaje, PR y rebase) y 5 `should_not_load` cercanos (qué hace `greet`, si hay bugs, qué hay sin commitear, añadir un docstring, escribir un test).

### M5. Casos informativos

**Problema.** La suite está saturada: los 7 casos de `git-workflow` pasan 3/3 (24/24 tras M1), así que no se ve si un cambio en el skill lo mejora. Para que la suite vuelva a medir hacen falta casos que el agente todavía falle a veces. Pero hoy cualquier caso que falle pone la sesión (y el check `evals` del PR) en rojo, así que no hay sitio para ellos.

**Lo que dicen las fuentes.** Anthropic, *Demystifying evals for AI agents*: separar los evals de capacidad (tareas que el agente aún no domina, con tasa baja, para ver el progreso) de los de regresión (lo que ya hace, cerca del 100 %, para que no se rompa), y pasar un caso de los primeros a los segundos cuando se satura.

**Decisión.** No dos suites: la suite actual ya es la de regresión. Basta con poder marcar un caso como informativo.

**Hecho** (sesión `20261008`, pendiente de commit). Cómo quedó:
- Campo `informative: true` en el caso del golden (booleano; otro valor es un error del loader). Los prompts de `skill_loading` no lo admiten.
- En un caso informativo, las métricas no estrictas (`expected_calls`, `outcome_checks`, `outcome`) son informativas; las prohibiciones (`forbidden_calls`, `rules_always_met`, `rules_always_met_judged`) siguen siendo obligatorias y estrictas. Lo decide `MetricSpec.informative(case)` y `CaseResult.informative`.
- En pytest, cada métrica informativa lleva `xfail(strict=False)`: `XFAIL` si no llega al mínimo, `XPASS` si llega, y ninguna pone la sesión en rojo. `summary.txt` encabeza sus informes con `XFAIL`/`XPASS`, y el informe dice "(minimum 2, informative)".
- `results.json` (schema 5): cada caso lleva `informative`, y cada métrica `informative` (si fallarla solo se informa).
- El comentario del PR marca las métricas informativas con `ℹ️`, las deja fuera de "N/M metrics passed" y añade una línea "Informative: X/Y metrics of informative cases reached the minimum". `evals-compare` las marca con `ℹ️` y no las cuenta en la tarjeta de cada sesión.
- Graduar un caso es manual: cuando sale `XPASS` de forma estable, se quita `informative: true`. El id no cambia, así que su historial en `evals-compare` sigue.
- Fuera de M5: escribir los casos difíciles de `git-workflow`. Candidatos: un conflicto contradictorio (el skill pide `git rebase --abort` y preguntar), el proceso RPI (trailers `Process:`/`Stage:` y la etiqueta `process: rpi`), un breaking change (`!` o `BREAKING CHANGE:`) y un PR aprobado con la etiqueta `do not merge`.

---

## Media

### M6. Juez de otra familia y fijo entre proveedores

**Descartada** (sesión `20261008`). No interesa por ahora. Se deja el ID para no renumerar las demás.

### M7. Ajustes del prompt y del schema del juez

**Descartada** (sesión `20261008`). No interesa por ahora. Se deja el ID para no renumerar las demás.

### M8. Criterios obligatorios en `outcome`

**Problema.** `outcome` tolera un criterio fallado por run (`max_misses = min(1, len - 1)`), y todos los criterios valen lo mismo. En un caso con varios criterios, algunos son lo esencial (que el conflicto se resolvió con intención) y otros detalles: hoy el agente puede fallar justo el esencial y pasar. Con M2 se dejó fuera a propósito poder marcar un criterio de calidad como imprescindible: es esto.

Alcance real hoy: ninguno. Cada caso de `git-workflow` tiene un único criterio en `expected_outcome`, y con uno solo la tolerancia ya es 0. M8 importa para los casos con varios criterios que vendrán (los difíciles de M5). `outcome_checks` no tiene el problema: ya exige que pasen todos sus checks.

**Lo que dicen las fuentes.** Las rúbricas con criterios críticos (que hacen fallar solos) y no críticos son lo habitual en las evals con juez; Hamel Husain recomienda un pass/fail por criterio importante antes que una puntuación agregada.

**Plan acordado** (sesión `20261008`, antes de implementar):
- **Formato.** Cada criterio de `expected_outcome` es un objeto de una sola clave, `required:` u `optional:`, con su texto. Un texto sin más se sigue aceptando y es `optional`. Cualquier otra forma es un error del loader.

  ```yaml
  expected_outcome:
    - required: "The conflict was resolved by intent: …"
    - optional: "The commit description is in the imperative mood"
  ```

- **Regla, dentro de un run.** Si falla un `required`, el run falla. Si pasan todos los `required`, se tolera que falle uno `optional`; si fallan dos o más, el run falla. Si fallan todos los criterios, el run nunca pasa (un caso con un solo criterio `optional` tiene que cumplirlo, como hoy).
- **Entre runs, nada cambia.** `outcome` sigue con `--min-passes`, no es estricta. En un caso `informative`, sigue siendo informativa aunque falle un `required`.
- **Solo `expected_outcome`.** `outcome_checks` sigue exigiendo todos sus checks, sin `optional`. `rules_always_met_judged` sigue siendo una lista de textos, todos obligatorios por definición; `required:`/`optional:` allí es un error.
- **El juez no lo sabe.** El prompt solo lleva el texto de cada criterio; ser obligatorio cambia cómo se cuenta, no la pregunta.
- **Informes.** En el terminal y en `summary.txt`, cada criterio obligatorio lleva `[required]`, y un run que falla por uno lo dice ("required criterion missed"). `results.json` pasa al schema 6: cada check de `outcome` lleva `required: true|false`. `evals-compare` marca la fila del criterio. El comentario del PR no lista criterios, así que no cambia.
- **Historial.** La clave de un criterio sigue siendo su texto: pasar de `optional` a `required` no abre fila nueva en `evals-compare`.
- **Golden.** Los cuatro criterios de `git-workflow` pasan a `required:` (el comportamiento no cambia: cada caso que tiene `expected_outcome` tiene uno).

**Ficheros.** `goldens.py` (forma del criterio y validación), `metrics/base.py` (un `Check` obligatorio hace fallar el `MetricResult`), `metrics/criteria.py`, `reporting/{terminal,results,compare}.py` y `compare.html`, `golden.yaml` de `git-workflow`, `tests/unit/test_{goldens,criteria,results,compare}.py`, `evals/README.md`.

**Hecho cuando.** Un criterio `required` hace fallar el run aunque sea el único fallo, y lo cubre un test unitario; el informe y `results.json` lo marcan.

**Hecho** (sesión `20261008`, pendiente de commit). Cómo quedó, respecto al plan:
- `goldens.py`: `Criterion(text, required)`; `expected_outcome` es una lista de `Criterion`. El loader rechaza un criterio mal formado (dos claves, otra clave, texto vacío) y los textos repetidos en un caso, porque el texto es su clave. `rules_always_met_judged` solo admite textos.
- `metrics/base.py`: `Check.required` (`None` en las métricas que no distinguen) y `MetricResult.missed_required`; un `MetricResult` no pasa si falla un check obligatorio, aunque la puntuación llegue al umbral. La regla de la tolerancia (`min(1, len - 1)`) no cambia: con los obligatorios cumplidos, solo pueden fallar los opcionales.
- El terminal y `summary.txt` muestran `[required]` delante del criterio y "required criterion missed" en el run que falla por uno. `evals-compare` pone `[required]` en la fila del criterio, según la sesión más reciente que lo tiene.
- Comprobado con un eval real de `commit-feature` (1 run): el criterio sale como `[required]` y `results.json` tiene schema 6 con `required: true`.

### M9. Calibrar el juez con etiquetas humanas

**Descartada** (sesión `20261008`). No interesa por ahora. Se deja el ID para no renumerar las demás.

### M10. Estadística: intervalos, pass^k y comparaciones emparejadas

**Descartada** (sesión `20261008`). No interesa por ahora. Se deja el ID para no renumerar las demás.

### M11. Detectar checks que no discriminan y casos inestables

**Descartada** (sesión `20261008`). No interesa por ahora. Se deja el ID para no renumerar las demás.

---

## Baja

### M12. Variantes de prompt

**Problema.** Cada caso tiene un único prompt, y el skill se mejora mirando cómo le va en esos mismos casos. El riesgo es ajustar el skill (sobre todo su `description`) a la forma exacta de pedir de la suite: pasa los evals y falla con usuarios que piden lo mismo con otras palabras. `skill_loading` (M4) cubre en parte la activación, pero no el comportamiento.

**Lo que dicen las fuentes.** El `skill-creator` de Anthropic, al optimizar la `description`, separa las consultas en un conjunto de entrenamiento y otro de prueba, y elige la versión por su resultado en el de prueba. Es lo mismo que en cualquier ajuste: lo que se usa para iterar no sirve para medir.

**Descartado de la propuesta original** (sesión `20261008`): el conjunto apartado (`held_out: true` y `--held-out`). No compensa la disciplina que exige.

**Plan acordado** (sesión `20261008`, antes de implementar):
- **Formato.** `prompt:` desaparece: todo caso lleva `prompts:`, una lista de al menos una variante, cada una `{id, text}` y nada más. El `id` lo escribe una persona y es la clave de la variante entre sesiones: retocar el texto o reordenar no rompe su historial. El loader rechaza un caso con `prompt`, sin `prompts`, con la lista vacía, con una variante mal formada o con ids repetidos en el caso. Los prompts de `skill_loading` no cambian (cada uno ya es un caso).

  ```yaml
  - id: commit-feature
    prompts:
      - id: directo
        text: "Commitea estos cambios."
      - id: coloquial
        text: "guarda esto en una rama y haz commit porfa"
      - id: indirecta
        text: "Ya he terminado farewell, déjalo guardado como toca."
  ```

- **Reparto en orden, sin azar.** El run i usa la variante i mod n: con `--runs 3` y 3 variantes, cada una corre una vez; con 3 runs y 2 variantes, v1, v2, v1; con 1 run, solo v1. Si alguna variante no llega a correr, el informe lo dice. Reproducible y comparable entre sesiones.
- **Aprobado sobre el total.** `--min-passes` sobre todos los runs del caso y N de N para las prohibiciones, como hoy. La tasa por variante solo se informa: nunca hace fallar un caso.
- **El juez ve el prompt del run**, no el del caso: cada run guarda qué variante lanzó.
- **Baseline.** Los runs sin el skill usan el mismo reparto (el run i, la misma variante con y sin el skill), y el informe da también con skill, sin skill y Δ por variante.
- **Informes.** Solo con más de una variante en el caso:
  - Terminal y `summary.txt`: cada run lleva su variante (`#2 [coloquial]`) y, debajo, la tasa por variante (`directo 1/1 · coloquial 0/1 · indirecta 1/1`); con baseline, también sin el skill y Δ por variante.
  - `results.json` (schema 7): cada run de `per_run` lleva `variant`; cada métrica, un bloque `variants` (`passes`, `runs` por variante); con baseline, lo mismo en cada lado y su `delta` por variante.
  - `evals-compare`: debajo de cada métrica, una subfila por variante con su historial. Los checks y criterios siguen agregados. Las sesiones anteriores no tienen subfilas.
  - El comentario del PR no cambia.
- **Golden de `git-workflow`.** Los 7 casos, trampa incluida, con 3 variantes: el prompt actual (`directo`) y dos de estos estilos, repartidos por el golden: registro coloquial, contexto de más y petición indirecta (el objetivo, sin el verbo técnico). En el idioma del skill (español), sin inglés. La trampa sigue pidiendo subir a `main`, y `open-pr-with-issue` sigue nombrando `ABC-123`.

**Ficheros.** `goldens.py` (`PromptVariant`, `prompts`, validación y docstring), `runtimes/runner.py` (`AgentTask` con el texto del run), `evaluation.py` (reparto, variante en `AgentRun` y tasa por variante con y sin el skill), `metrics/criteria.py` (el prompt del run para el juez), `session.py`, `reporting/{terminal,results,compare}.py` y `compare.html`, `golden.yaml` de `git-workflow`, `evals/README.md`, y tests unitarios del loader, del reparto, de la tasa por variante, del schema 7 y de `evals-compare`.

**Hecho cuando.** Los 7 casos de `git-workflow` tienen 3 variantes, cada run dice cuál lanzó, el juez ve ese texto, y el terminal, `results.json` y `evals-compare` dan la tasa por variante (y su Δ con `--baseline`).

**Hecho** (sesión `20261008`, pendiente de commit). Cómo quedó, respecto al plan:
- `goldens.py`: `PromptVariant(id, text)`; `GoldenCase.prompts`, `has_variants` y `variant(i)`. `prompt:` es ahora una clave desconocida (error). Los prompts de `skill_loading` tienen una única variante, `prompt`.
- La variante del run va en `AgentRun.variant`, y su texto en `Trace.prompt`, que es lo que lee el juez. Los logs de un caso con variantes llevan su id: `<caso>-[baseline-]<variante>-<i>-…`.
- `CaseResult.by_variant()` y `Baseline.variant_deltas()`. Terminal y `summary.txt`: `#2 [coloquial]`, `by variant: …` y `baseline by variant: directo 1/1 vs 0/1 (+100 pp) · …`. `results.json` schema 7: `variant` en cada run, `variants` en cada métrica y `variant_deltas` en su baseline. `evals-compare`: subfilas `↳ prompt <id>` al desplegar la métrica (su JS pasa `node --check`; la página no se ha revisado a mano en el navegador).
- Primera medición (sesión `20261008-122314`, Claude Code, `claude-sonnet-5-5`, 3 runs, `--baseline`): 41/41 con el skill, en todas las variantes. Sin el skill, los 7 casos fallan. `rebase-onto-main-with-conflict`, que antes pasaba sin el skill, ahora falla: sin el skill, la variante `coloquial` ("trae lo nuevo de main a mi rama y súbela") hace un merge en vez de un rebase. Coste: 3,77 $.

### M13. Eficiencia como métrica

**Problema.** Los datos ya se recogen; lo que falta es agregarlos por caso y llevarlos a donde se comparan. Cada run guarda en su `Trace` los turnos por agente (`turns`), las llamadas a herramientas (`tool_calls()`) y un `Usage` con tokens (entrada, salida, razonamiento, caché), coste, AI credits (Copilot) y tiempo. Dónde se ve hoy cada cosa:

| Medida | Por run | Por caso | Sesión entera | `results.json` | `evals-compare` | Con y sin skill |
|---|---|---|---|---|---|---|
| Turnos y llamadas | Terminal: línea de cada run y tabla `eval turns` | – | – | – | – | Filas aparte en `eval turns` (`<caso> (without skill)`), sin Δ |
| Tokens, coste, tiempo | Solo en la `Trace`, no se muestra | – | Tabla `eval usage` (agente y juez) | Bloque `usage` de la sesión | Solo el tiempo de reloj | Sumado todo junto en el total del agente |

Así, lo único comparable entre sesiones es el total de la sesión, que mezcla casos, variantes y los runs sin el skill (con `--baseline`, el coste del agente casi se duplica sin que se vea de dónde). Un cambio del skill que lo hace igual de bien con el doble de tokens, o un skill que añade diez turnos a cada commit, no se ve: ni por caso, ni en el histórico, ni frente al baseline.

**Lo que dicen las fuentes.** Anthropic, *Demystifying evals*: además de si la tarea sale bien, medir turnos, llamadas, tokens y latencia, porque un agente que acierta a cualquier coste no es el que se quiere. `claude plugin eval` saca también el coste con y sin el plugin.

**Propuesta.** No hace falta recoger nada nuevo: es agregar lo que ya está en cada `Trace`.
- Una métrica informativa por caso, `efficiency`, con la mediana por run de turnos, llamadas a herramientas, tokens totales, coste y tiempo; no pasa ni falla. Por run en `per_run`, para poder ver el run que se dispara.
- Con `--baseline`, su Δ (el skill cuesta X tokens más o menos).
- Opcional en el golden, un `budget` por caso (`max_turns`, `max_tool_calls`) que, si se supera, se informa como aviso; si con el tiempo resulta útil, puede pasar a ser un check.
- En `results.json`, por caso, y en `evals-compare`, una fila por medida.

**Ficheros.** `evaluation.py`, `metrics/` (o fuera de las métricas si no pasa ni falla), `goldens.py` (si se añade `budget`), `reporting/*` y `compare.html`, tests unitarios, `evals/README.md`.

**Hecho cuando.** Cada caso tiene sus turnos, llamadas, tokens y coste en `results.json` y en `evals-compare`, con su Δ en el baseline.

**Plan acordado** (sesión `20261008`, antes de implementar). Cambia la propuesta en esto:
- **Fuera de `METRICS`.** No es una `MetricSpec` ni crea tests en pytest: un id que nunca falla solo añadiría ruido. Es un bloque `efficiency` por caso, calculado de sus runs al cerrar la sesión.
- **Sin `budget`.** Queda fuera de M13: primero hay que ver las cifras durante unas sesiones, porque sin datos cualquier límite sería inventado.
- **Medidas, por run.** Turnos y llamadas a herramientas, cada uno sumando el agente principal y los subagentes. Tokens totales (`Usage.total_tokens`). Coste: `cost_usd` en Claude y `ai_credits` en Copilot; si ningún run da un valor, queda vacío (`null`, "–"), no 0. Tiempo: el `duration_s` del run.
- **Mediana por caso.** Se calcula solo con los runs sin error (otro modelo, el runtime falla), que tampoco cuentan para las métricas; su gasto sí sigue en el total de la sesión. Con N par, es la media de los dos centrales. Sin ningún run válido, todo sale vacío. Los valores de cada run, con su variante y su error si lo tuvo, van en `per_run`.
- **Sin desglose por variante.** Con 3 runs y 3 variantes, la mediana de cada variante sería un solo run, que ya está en `per_run`.
- **Baseline.** Se calcula la misma mediana con los runs sin el skill, y por cada medida un Δ absoluto y en porcentaje (con skill menos sin skill): tokens 160k frente a 95k, Δ +65k (+68 %). Si la cifra sin el skill es 0 o vacía, el porcentaje sale vacío. Es informativo, como todo el baseline.
- **`skill_loading`.** Sus prompts llevan eficiencia como cualquier caso, sin baseline (no lo tienen).
- **Juez.** Su coste por caso queda fuera: mide el coste del eval, no el del skill, y atribuirlo exige tocar `Judge` y las métricas juzgadas.
- **Total de la sesión.** Se separa el uso de los runs sin el skill: la tabla `eval usage` tiene la fila `agent` (con el skill) y, con baseline, `agent without skill`; `results.json` añade `usage.agent_without_skill`. El Markdown suma las dos en su línea de uso.
- **Informes.**
  - Terminal y `summary.txt`: la tabla `eval efficiency` sustituye a `eval turns`. Tiene una fila por caso con la mediana de cada medida; con baseline, también la fila sin el skill y el Δ. El desglose de turnos por agente sigue en la línea de cada run.
  - `results.json`, schema 8: cada caso lleva `efficiency` con `with_skill` (`runs` válidos, `median`, `per_run`), `without_skill` (igual, solo con baseline) y `delta` (`{absolute, percent}` por medida).
  - `evals-compare`: en cada golden, una sección "efficiency" con una fila por medida y una celda por sesión ("9 turns"); con baseline, la celda añade "w/o 6 · Δ +3 (+50 %)". Las sesiones anteriores al schema 8 salen con "–".
  - El comentario del PR no cambia, salvo la línea de uso.

**Ficheros.** `evaluation.py` o un módulo nuevo `efficiency.py` (cálculo por run, mediana y Δ), `session.py` (uso con y sin el skill; `SessionSummary.efficiency` en vez de `turns`), `tests/conftest.py` (la tabla que imprime), `reporting/terminal.py` (`format_efficiency` y la fila sin el skill de `format_usage`), `reporting/results.py` (schema 8), `reporting/compare.py` y `compare.html` (sección "efficiency"), `reporting/markdown.py` (línea de uso), `evals/README.md`, y tests unitarios: mediana sin los runs con error, coste vacío, Δ con 0 sin el skill, schema 8, tabla del terminal y filas de `evals-compare`.

**Hecho** (sesión `20261008`, pendiente de commit). Cómo quedó, respecto al plan:
- Módulo nuevo `efficiency.py`: `run_measures`, `Efficiency` (`valid` y `median()`), `CaseEfficiency` (`delta()`) y `Delta(absolute, percent)`. `EvalSession.efficiencies()` los arma con los runs de cada caso; `agent_usage(without_skill=)` separa el uso.
- Se quitan `format_turns` y su test: la tabla `eval efficiency` la sustituye. El terminal escribe los Δ como `+3 (+50 %)`, `-1m05s (-20 %)` o `0 (0 %)`, y sin porcentaje si la cifra sin el skill es 0.
- `results.json`, schema 8: `efficiency.with_skill`/`without_skill` con `runs` (válidos), `total_runs`, `median` y `per_run` (variante, error y medidas de cada run), y `delta` por medida (`{absolute, percent}` o `null`).
- `evals-compare`: filas `efficiency · <medida>` al final de cada golden, sin color ni estrella (gastar menos no siempre es mejor), y `efficiency` en el filtro de métricas. Su JS pasa `node --check`; la página no se ha revisado a mano en el navegador.
- Comprobado con un eval real de `commit-feature` (1 run, `--baseline`, sesión `20261008-125854`): con el skill, 3 turnos, 3 llamadas y 61.617 tokens (0,05 $); sin él, 3 turnos, 2 llamadas y 56.415 tokens. Δ: tokens +9 %, coste +36 %.

### M14. Contenedor por run

**Descartada** (sesión `20261008`). No interesa por ahora. Se deja el ID para no renumerar las demás. En CI cada job ya corre en una VM efímera; lo que quedaba (aislar los runs entre sí, cortar la red, no heredar las credenciales del job) no compensa por ahora.

**Problema.** El sandbox es una carpeta temporal con su configuración de git y su `PATH`, pero las herramientas del agente se ejecutan en la máquina, shell incluida (lo dice el README). Un agente que se equivoca puede tocar fuera del sandbox (el `~` del usuario, otros repos), llegar a la red aunque las fixtures no lo necesiten, y el resultado depende de lo que haya instalado en cada máquina (versión de git, herramientas en el `PATH`). Los runs paralelos también comparten la máquina.

**Lo que dicen las fuentes.** Los benchmarks de agentes ejecutan cada tarea en su contenedor (SWE-bench, Terminal-Bench): aislamiento y el mismo entorno en cada run y en cada máquina. Anthropic, *Demystifying evals*: el entorno de cada intento tiene que estar limpio y aislado para que un run no afecte a otro.

**Propuesta.**
- Una imagen con el runtime (Claude Code o Copilot CLI en su versión fijada), git, bash y lo que necesiten las fixtures. Cada run, un contenedor con el sandbox montado, sin red salvo hacia la API del modelo (proxy o lista de dominios), y que se destruye al acabar.
- Las credenciales del runtime entran por variable o por fichero montado de solo lectura, no la configuración entera del usuario. Es lo más difícil: el login de Claude Code (OAuth) y el de Copilot (llavero) no están pensados para un contenedor.
- Opcional (`--isolation container`) al principio, para comparar con lo de hoy; si los resultados coinciden, por defecto en CI.

**Ficheros.** `evals/Dockerfile` (nuevo), `sandbox.py`, `runtimes/runner.py` y los runners de cada runtime, `runtimes/process.py`, `config.py`, `.github/workflows/evals.yml`, `evals/README.md`.

**Hecho cuando.** Los evals se pueden ejecutar con cada run en su contenedor sin red general, con los mismos resultados que sin contenedor, y CI lo usa.

### M15. Casos desde fallos reales

**Descartada** (sesión `20261008`). No interesa por ahora. Se deja el ID para no renumerar las demás.

**Problema.** Los casos los escribió quien escribió el skill, pensando en lo que el skill debe hacer. Los fallos que importan son los que aparecen usándolo: un commit con un tipo raro, un PR sin la plantilla, un rebase que el agente resolvió mal. Hoy no hay un camino para que un fallo real acabe en la suite.

**Lo que dicen las fuentes.** Anthropic, *Demystifying evals*: empezar por 20–50 tareas sacadas de fallos reales y seguir añadiendo a partir de ellos. Hamel Husain: el análisis de errores (leer sesiones reales, clasificar los fallos) va antes de escribir evals, y decide cuáles escribir.

**Propuesta.**
- Un proceso, escrito en el README: cuando un skill falla en uso real, se reduce el caso a una fixture (sin código ni nombres de clientes: el repo es público), se añade al golden como `informative: true` (M5) y se gradúa cuando el skill lo arregla.
- Una plantilla de issue ("fallo de un skill") que pida el prompt, lo que hizo el agente y lo que debía hacer.
- Opcional: un script que, a partir de un log de Claude Code o Copilot, proponga el esqueleto del caso (prompt, llamadas, estado de git), para no reconstruirlo a mano.
- Repasar los fallos cada cierto tiempo y agruparlos por tipo, para ver qué parte del skill falla más.

**Ficheros.** `evals/README.md`, `.github/ISSUE_TEMPLATE/` (nuevo), opcionalmente un script en `src/marketplace_evals/`, y los `golden.yaml` que reciban casos.

**Hecho cuando.** Está escrito el proceso, existe la plantilla, y al menos un caso de `git-workflow` viene de un fallo real. Después, continuo.

### M16. Evaluar el harness completo

**Problema.** Cada run carga solo el plugin del skill evaluado (`--plugin-dir`), con `--setting-sources project`, sin MCP del usuario y en una fixture mínima. Así se mide el skill aislado, pero un usuario lo tiene junto a los demás plugins del marketplace, con su `CLAUDE.md`/`AGENTS.md`, sus MCP y un repo real. Ahí aparecen problemas que la suite no ve: dos skills cuyas `description` compiten (se carga el que no es), instrucciones del proyecto que contradicen al skill, o tareas que necesitan dos skills a la vez.

**Lo que dicen las fuentes.** Terminal-Bench y los informes de Anthropic sobre Claude Code evalúan el conjunto agente + harness + herramientas, porque el resultado depende de todo junto. SkillsBench: los skills empeoran algunas tareas, y parte de eso es interferencia con el contexto que ya tiene el agente.

**Propuesta.**
- Un modo de sesión, `--harness full`, que carga todos los plugins del marketplace (o una lista), no solo el del skill evaluado.
- Fixtures más realistas: un repo con `CLAUDE.md`/`AGENTS.md` con convenciones propias (algunas en conflicto con el skill, para ver qué gana), varios ficheros y una historia de git más larga.
- Goldens que no pertenecen a un skill, en `evals/harness/`, para tareas que cruzan skills (crear una rama, cambiar código, commitear y abrir el PR).
- `skill_loading` (M4) en este modo: que con todos los plugins cargados se siga cargando el skill correcto.

**Ficheros.** `config.py`, `sandbox.py` (copiar varios plugins), los runners (varios `--plugin-dir`), `goldens.py` y `paths.py` (goldens fuera de `plugins/`), `tests/evals/test_evals.py`, `evals/harness/` (nuevo), `evals/README.md`.

**Hecho cuando.** Hay al menos un golden que corre con todos los plugins del marketplace cargados y una fixture con instrucciones de proyecto, y `skill_loading` se puede correr en ese modo.

### M17. Probar `claude plugin eval` en paralelo

**Problema.** Claude Code trae `claude plugin eval`, que ejecuta suites de evals de un plugin, compara con y sin el plugin (columnas WITH, W/OUT y Δ, y `--ablation`) y saca un informe. Hace parte de lo mismo que este framework, y no sabemos si sus resultados coinciden con los nuestros, si cubre algo que nos falta o si mantener lo nuestro compensa. Solo sirve para Claude Code, así que no sustituye la parte de Copilot.

**Lo que dicen las fuentes.** La documentación de `claude plugin eval` y de `/skill-doctor`.

**Propuesta.**
- Escribir una suite de `claude plugin eval` para `commons/git-workflow` con los mismos casos (o los que su formato permita) y ejecutarla junto a `uv run pytest -m eval --provider claude --baseline -k git-workflow`, con los mismos modelos.
- Comparar: casos que pasan en uno y no en el otro, Δ del baseline, coste, y lo que su formato no puede expresar (checks de shell sobre el estado, `before`, prohibiciones estrictas, stubs) o expresa mejor.
- Apuntar aquí la conclusión: ignorarlo, usarlo como comprobación cruzada en local, o adoptar partes (su formato de suite, su informe).

**Ficheros.** Ninguno del framework; la suite de prueba en una rama o en `evals/experiments/`, y la conclusión en este documento.

**Hecho cuando.** Está apuntado, con números de una ejecución de los dos sobre `git-workflow`, qué hace `claude plugin eval` igual, mejor o peor, y qué se decide.
