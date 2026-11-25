# Centinela-CO: paquete de arranque para Tiny Tapeout GF

Núcleo de decisión de dos canales que retira el permiso de una válvula de gas cuando la dosis
de monóxido de carbono alcanza 2,880 ppm·min. Diseño didáctico para el IEEE Open Silicon
Bootcamp 2026. **No es un componente de seguridad certificado.**

## Qué trae

| Archivo | Va en el repositorio como | Qué es |
|---|---|---|
| `src/project.v` | `src/project.v` | RTL en Verilog, un solo módulo |
| `src/config.json` | `src/config.json` | Configuración de la plantilla; solo cambia `CLOCK_PERIOD` de 20 a 100 ns |
| `info.yaml` | `info.yaml` | Título, reloj de 32768 Hz, 1x1, nombres de pines |
| `docs/info.md` | `docs/info.md` | Documentación pública, en inglés |
| `test/test.py` | `test/test.py` | 26 pruebas cocotb |
| `test/tb.v` | `test/tb.v` | Banco de pruebas; solo guarda las señales de los pines |

`test/Makefile` y `test/requirements.txt` son los de la plantilla, sin cambios.

## Pasos

1. Crea un repositorio público con **Use this template** desde
   https://github.com/TinyTapeout/ttgf-verilog-template
2. En **Settings → Pages → Source** elige **GitHub Actions**.
3. Sustituye los archivos de la tabla por los de este paquete.
4. Cambia el nombre del módulo por uno único que incluya tu usuario de GitHub. Aparece en cuatro
   archivos: `src/project.v`, `test/tb.v`, `info.yaml` y `docs/info.md`. En Linux o macOS:

   ```
   grep -rl tt_um_utcj_centinela_co . | xargs sed -i 's/tt_um_utcj_centinela_co/tt_um_TUUSUARIO_centinela_co/g'
   ```

5. Pon el nombre de tu equipo en `author` (info.yaml) y en la cabecera de `project.v`.
6. Haz commit. En la pestaña **Actions** deben quedar en verde `test`, `docs` y `gds`.

## Qué revisar en Actions

- `test`: 26 pruebas, todas PASS (tarda unos 5 minutos).
- `gds` → `gl_test`: 17 PASS y 9 SKIP. Las nueve omitidas son las dos más lentas y las siete de
  inyección de fallas, que necesitan forzar señales internas.
- `gds` → resumen de síntesis: **125 flip-flops**. Si hay menos, la síntesis fusionó registros.
- `gds` → utilización: esperada entre 50 % y 63 %. Por debajo de 70 % está bien.
- `gds` → `precheck`: verde.

Si `gds` falla por densidad (`GPL-0302`), sube `PL_TARGET_DENSITY_PCT` a 70 en `src/config.json`.

## Correrlo en tu computadora

```
sudo apt-get install iverilog
pip install cocotb==2.1.0 pytest
cd test && make
```

## Resultados obtenidos antes de entregar (25-nov-2026)

- Verilator `--lint-only -Wall -Wno-DECLFILENAME` (el aviso que LibreLane también desactiva): sin avisos.
- cocotb 2.1 + Icarus sobre el RTL: 26 de 26 pruebas.
- Síntesis con Yosys 0.71 sobre la librería `gf180mcu_fd_sc_mcu7t5v0` a 3.3 V: 758 celdas,
  125 flip-flops, 20,602 µm² (39.6 % del núcleo de un tile 1x1).
- Simulación a nivel de compuertas de esa síntesis: 17 pruebas pasan, 9 omitidas a propósito.
- Revisión independiente: 1,576 fallas stuck-at inyectadas en el netlist. En modo campo ninguna
  deja la válvula permitida más allá de la dosis; en modo demo quedan 21, todas en el divisor de
  reloj. Cuatro mutantes (quitar una función de seguridad cada uno) hacen fallar al menos una prueba.
- Flujo físico local, equivalente al oficial pero no idéntico (OpenLane 1 con el PDK gf180mcuC en
  lugar de LibreLane 3.0.14 con gf180mcuD): GDS completo en el tile 1x1 con la configuración de
  Tiny Tapeout. 125 flip-flops, 928 celdas funcionales, 54.5 % de utilización, 0 violaciones de
  ruteo, 0 de DRC (Magic y las reglas de GF en KLayout), LVS limpio, 0 antenas. Holgura de setup
  de +63.6 ns o más y de hold de +0.19 ns o más en las tres esquinas. Cuatro pines pasan el límite
  de slew (3.14 ns contra 3.00) solo en la esquina lenta; LibreLane lo reporta como aviso.
- Simulación de compuertas del netlist final de ese flujo (con buffers y árbol de reloj):
  17 pruebas pasan, 9 omitidas a propósito.
- **El GDS que se fabrica lo genera GitHub Actions.** El flujo local dice que el diseño cabe y
  cierra; no sustituye al oficial.

Licencia: Apache-2.0, igual que la plantilla.
