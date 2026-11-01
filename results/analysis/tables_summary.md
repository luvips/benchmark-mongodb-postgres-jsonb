# Tablas Consolidadas de Resultados (Mediana y Rango `[min, max]`)

## 1. Matriz Principal y Anexo (`Q1`, `Q2`, `Q1_relaxed`, `Q2_relaxed`)

| Consulta | Perfil | Motor | `c` | Corridas | Estado(s) | QPS (med `[min, max]`) | P50 ms | P95 ms | P99 ms (med `[min, max]`) | $N_{\min}$ | P99 Confiable | CFS Throttled (s) | Max RSS (MB) | Spill |
|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| `Q1` | `PROFILE_A_BASELINE` | `mongodb` | 10 | 1 | `ok` | 1.003 | 9731.0 | 13445.7 | 14322.6 | 301 | Sí | 2.46 | 736.3 | 0/1 |
| `Q1` | `PROFILE_C_SEVERE` | `mongodb` | 1 | 2 | `ok` | 0.522 [0.517, 0.527] | 1853.4 | 2292.8 | 2815.8 [2597.7, 3033.9] | 155 | Sí | 10.60 [10.55, 10.65] | 426.4 [426.3, 426.4] | 0/2 |
| `Q1` | `PROFILE_C_SEVERE` | `postgresql` | 1 | 1 | `spill` | 0.140 | 7084.0 | 7954.6 | 7985.6 | 42 | No ($N<100$) | 276.00 | 100.7 | 1/1 |
| `Q2` | `PROFILE_B_MEDIUM` | `postgresql` | 50 | 1 | `ok` | 2.470 | 19620.6 | 25696.2 | 31965.1 | 741 | Sí | 582.71 | 581.4 | 0/1 |
| `Q2` | `PROFILE_C_SEVERE` | `postgresql` | 50 | 1 | `ok` | 0.320 | 153427.0 | 243298.3 | 272961.3 | 96 | No ($N<100$) | 820.79 | 575.6 | 0/1 |

## 2. Métrica M3: Huella de CPU y Memoria `(a) Pico + (b) Integral` por cada $1\,000$ Documentos

| Consulta | Perfil | Motor | `c` | $\text{CPU}_{1\text{k}}$ ($\text{CPU}\cdot\text{s}/10^3\text{ docs}$) | `(a)` $\text{Mem}_{\text{peak}}$ ($\text{MB}$) | `(b)` $\text{Mem}_{1\text{k}}$ ($\text{MB}\cdot\text{s}/10^3\text{ docs}$) | Escrituras Bloque Ventana (MB) |
|---|---|---|---:|---:|---:|---:|---:|
| `Q1` | `PROFILE_A_BASELINE` | `mongodb` | 10 | 0.00790 | 736.3 | 1.472 | 2.2 |
| `Q1` | `PROFILE_C_SEVERE` | `mongodb` | 1 | 0.00380 [0.00380, 0.00390] | 426.4 [426.3, 426.4] | 1.575 [1.565, 1.586] | 2.5 |
| `Q1` | `PROFILE_C_SEVERE` | `postgresql` | 1 | 0.01410 | 323.9 | 3.408 | 58806.7 |
| `Q2` | `PROFILE_B_MEDIUM` | `postgresql` | 50 | 0.00160 | 1147.9 | 0.924 | 0.2 |
| `Q2` | `PROFILE_C_SEVERE` | `postgresql` | 50 | 0.00630 | 872.4 | 5.426 | 0.1 |
