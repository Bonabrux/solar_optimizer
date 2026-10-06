# Solar Optimizer — contexto de trabajo con Claude

Este archivo resume el estado de una serie de mejoras que se están implementando
sobre esta integración de Home Assistant (fork personal de jmcollin78/solar_optimizer),
para poder retomar el trabajo con Claude desde otra sesión/dispositivo sin perder contexto.

Repo: `bonabrux/solar_optimizer`
Rama de trabajo actual: `claude/three-phase` (Fase 3), creada desde la rama del PR #215
`feature/manual-override-measured-power`, que NO se toca (por si hay que volver a ese código).
La rama vieja `claude/solar-optimizer-improvements-6ynf06` quedó como histórico (Fases 1-2).
No se debe abrir Pull Request hasta que el usuario lo pida explícitamente — probar
todo a fondo primero. Todo el trabajo se pushea a esa rama en el fork.

## Estado del PR (06/10/2026)

**PR abierto: https://github.com/jmcollin78/solar_optimizer/pull/215** con Fase 1 + Fase 2 +
ajustes del 06/10 (sensor de potencia medida, cache-busting, traducción es).
- Rama del PR: `feature/manual-override-measured-power` en el fork, creada desde
  `upstream/main`, **un solo commit** a nombre del usuario. Esta rama `claude/...`
  NO se usa para PRs: tiene commits con autor "Claude" y el CLAUDE.md.
- Regla para PRs futuros: rama limpia desde `upstream/main`, aplicar el diff sin
  CLAUDE.md ni carpetas internas, sin referencias a "Fase"/puntos internos ni a
  Claude en código, tests, commits o descripción. PR en inglés (repo externo).
- Si el maintainer pide cambios: hacerlos en `feature/manual-override-measured-power`
  y replicarlos acá para mantener ambas ramas alineadas.

## Cómo retomar esto (sesión nueva, sin memoria de la conversación anterior)

- **Estado real al día de hoy**: la rama `claude/solar-optimizer-improvements-6ynf06`
  ya está pusheada a `origin` en GitHub (`git push -u origin ...` confirmado
  exitoso), con Fase 1 y Fase 2 completas y commiteadas. Todavía **no hay
  Pull Request abierto**.
- Si esta sesión es nueva (contenedor efímero recién creado, o PC), la rama YA
  existe en remoto — no hay que reconstruir nada, solo traerla:
  ```bash
  git fetch origin claude/solar-optimizer-improvements-6ynf06
  git checkout claude/solar-optimizer-improvements-6ynf06
  ```
- Si al pushear aparece un error 403 tipo "Claude doesn't have GitHub access to
  Bonabrux/solar_optimizer" (ya pasó una vez en esta sesión y el usuario lo
  resolvió instalando/reconectando la app de Claude en GitHub), pedirle al
  usuario que repita esa autorización: https://github.com/apps/claude/installations/select_target
  o reconectar desde https://claude.ai/customize/connectors?auth_start=github&auth_start_force=1
- Próximo paso real pendiente: el usuario va a probar todo a fondo en su
  instalación real de HA (tiene previsto ~2 semanas). Hasta que no confirme que
  quedó conforme, no corresponde tocar Fase 3/4 salvo que lo pida explícitamente,
  y bajo ningún concepto abrir un PR sin que lo pida.

## Arquitectura (para orientarse rápido)

- `coordinator.py` (`SolarOptimizerCoordinator`): refresca periódicamente
  (`_async_update_data`), lee consumo/producción/costos/batería, corre el algoritmo
  y aplica las decisiones sobre cada `ManagedDevice`.
- `managed_device.py` (`ManagedDevice`): representa un dispositivo gestionado. Tiene
  `activate()/deactivate()/change_requested_power()` (pasan por `_apply_action`),
  `is_enabled` (flag "Enable" — si está en False el algoritmo lo ignora por completo
  en `simulated_annealing_algo.py::recuit_simule`), y ya traía un mecanismo de
  activación forzada (`start_forced`/`stop_forced`/`expire_forced_activation`,
  servicios `start_device`/`stop_device`) que pausa el algoritmo con un timer opcional.
- `switch.py`: `ManagedDeviceSwitch` (switch.solar_optimizer_<device>, refleja/controla
  el estado real) y `ManagedDeviceEnable` (switch.enable_solar_optimizer_<device>,
  pausa/reanuda el algoritmo para ese dispositivo).
- `sensor.py`: `TodayOnTimeSensor` (contador diario de tiempo encendido, con
  `RestoreEntity`) y `SolarOptimizerSensorEntity` (sensores centrales: best_objective,
  total_power, power_production, power_consumption, battery_soc).
- `simulated_annealing_algo.py`: el algoritmo de recocido simulado. Optimiza un único
  "neto" global (consumo - producción), no conoce el concepto de fases.
- `frontend/solar-optimizer-card.js`: la tarjeta Lovelace.
- Tests en `tests/`, con arnés en `tests/commons.py` y `tests/conftest.py`.

## Pedido original del usuario (5 puntos)

1. Al reiniciar HA se pierde la memoria del tiempo de uso diario de cada dispositivo.
2. La barra de potencia del widget muestra `power_max` fijo, no el consumo real.
3. Si el usuario prende/apaga un dispositivo a mano, el algoritmo lo vuelve a su
   estado en el próximo ciclo, sin respetar la decisión manual.
4. No hay soporte trifásico — el algoritmo solo ve consumo/producción global.
5. El uso de batería no tiene una política clara (cargas primero vs. batería primero).

Después se agregó: mostrar el override como sensor, que el algoritmo lo respete
hasta que se libere (por el algoritmo, por tiempo, por el usuario o por una
automatización), y que las acciones de Solar Optimizer queden atribuidas en el
historial/logbook de HA (hoy no "anuncia" que fue él quien actuó). También:
soporte multi-moneda / arreglar `best_objective`, que en realidad nunca fue un
valor monetario (estaba mal etiquetado como `SensorDeviceClass.MONETARY` con "€").

## Plan acordado

- **Fase 1** (fixes chicos, bajo riesgo) — **HECHA en esta rama**
- **Fase 2** (override manual + moneda) — **HECHA en esta rama**
- **Fase 3** (soporte trifásico) — **HECHA en `claude/three-phase`** (06/10/2026), pendiente
  de prueba real del usuario. Ver sección "Fase 3" abajo.
- **Fase 4** (política de uso de batería por dispositivo) — **HECHA en
  `claude/three-phase`** (06/10/2026), pendiente de prueba real. Ver sección "Fase 4".

## Qué se implementó en Fase 1

1. **`sensor.py`** — `TodayOnTimeSensor.async_added_to_hass`: al restaurar el valor
   persistido, ahora llama `self._device.set_on_time(...)` de inmediato en vez de
   esperar el primer evento o el tick de 1 minuto. Antes había una ventana de hasta
   60s post-reinicio donde `ManagedDevice._on_time_sec` seguía en 0.
2. **`managed_device.py`** — `set_current_power_with_device_state()`: si hay
   `power_entity_id` configurado, se usa siempre para `current_power` (antes solo se
   usaba si `can_change_power` era True; si no, se hardcodeaba `power_max`). Ahora
   `power_max`/`power_min` quedan solo como fallback de planificación cuando no hay
   entidad de monitoreo o su estado no está disponible.
3. **`sensor.py` + `card.js`** — el sensor `best_objective` ya no tiene
   `device_class=MONETARY` ni unidad `"€"`. Es un score adimensional del algoritmo
   (mezcla de coeficientes de costo import/export + prioridad, no una cifra de
   dinero) — estaba mal etiquetado independientemente de la moneda del usuario. La
   card ahora lo muestra como número plano (3 decimales), sin símbolo de moneda.

## Qué se implementó en Fase 2 (override manual)

Diseño: reusa toda la plumbing existente del switch **Enable** (`is_enabled`) en vez
de crear una entidad nueva de pausa — cuando `is_enabled=False` el algoritmo ya
excluye completamente al dispositivo (`recuit_simule`), así que no hizo falta tocar
el algoritmo.

- **`managed_device.py`**: nuevos campos `_override_active`, `_override_since`,
  `_override_baseline_state` (el estado que SO pedía *antes* del override — se usa
  para saber cuándo el override quedó resuelto), `_last_commanded_state` (último
  estado on/off que SO mismo pidió vía `_apply_action`), `_last_context` (ver más
  abajo). Métodos nuevos: `trigger_manual_override(baseline=None)`,
  `clear_override()`, `clear_override_if_resolved()`, `check_for_manual_override()`,
  `set_override_state()` (restauración post-reinicio). `set_enable(True)` limpia el
  override automáticamente (cubre "acción del usuario" y "automatización" si
  encienden el switch Enable).
- **`coordinator.py`**: en cada refresh (`_async_update_data`) llama
  `device.check_for_manual_override()` — detecta si el estado real del dispositivo
  ya no coincide con lo que SO pidió (y no es una sesión de `start_forced`, y no está
  en la ventana de espera `is_waiting`), y si un override activo ya se resolvió.
  Nuevo listener `_async_on_raz_time` (a la hora de reset diaria) que limpia
  cualquier override pendiente — mismo mecanismo horario que ya usaba el contador
  de on_time.
- **`switch.py`**: clickear el switch "Active" (`switch.solar_optimizer_<device>`)
  directamente ahora dispara `trigger_manual_override()` de inmediato (con el baseline
  capturado *antes* de aplicar el cambio) — esto es lo que resuelve el bug original
  del usuario ("apago el dispositivo y a los pocos segundos se vuelve a prender").
  Apagar el switch durante una sesión de `start_forced` sigue sin considerarse
  override (comportamiento preexistente intacto, cubierto por los tests de
  `test_timed_activation.py`).
- **`binary_sensor.py`** (nuevo archivo, nueva plataforma agregada a `PLATFORMS` en
  `const.py`): `binary_sensor.solar_optimizer_override_<device>`, con atributos
  `override_since` y `override_baseline_state`. Restaura su estado post-reinicio
  (`RestoreEntity`).
- **Servicio `solar_optimizer.clear_override`** (`__init__.py`, `const.py`,
  `services.yaml`): para que una automatización pueda liberar el override sin tener
  que saber que por debajo usa el switch Enable.
- **Las 4 formas de liberar el override**, todas implementadas:
  1. *El propio algoritmo*: `clear_override_if_resolved()` en cada ciclo del
     coordinador (si el estado real vuelve a coincidir con el baseline).
  2. *Tiempo*: `_async_on_raz_time`.
  3. *Acción del usuario*: matching de estado (cubre el punto 1), o prender el
     switch Enable a mano.
  4. *Automatización*: servicio `clear_override`, o prender el switch Enable desde
     un script/automatización (mismo mecanismo que el punto 3).
- **Atribución en historial/logbook**: `do_service_action`/`do_event_action` en
  `managed_device.py` ahora aceptan y propagan un `Context`. `_apply_action` crea un
  `Context()` nuevo por acción y lo guarda en `device.last_context`. En `switch.py`,
  `ManagedDeviceSwitch._handle_coordinator_update` llama
  `self.async_set_context(device.last_context)` antes de escribir su propio estado,
  para que el historial de la entidad real controlada pueda trazarse hasta
  `switch.solar_optimizer_<device>`. Nota realista: HA reserva el texto literal
  "Triggered by automation X" para entidades de automatización/script — esto logra
  el máximo nivel de trazabilidad que HA permite para una integración custom, no
  ese texto exacto.
- **Ya existía y no hizo falta tocar**: el contador `on_time_today` cuenta el
  tiempo real encendido mirando `device.is_active`, sin importar quién lo prendió —
  o sea que el tiempo de un override "encendido" ya se contaba correctamente antes
  de este trabajo (se agregó un test para dejarlo verificado, no solo asumido).

## Tests nuevos

- `tests/test_fase1_fixes.py` — los 3 fixes de Fase 1.
- `tests/test_manual_override.py` — detección/resolución de override (las 4 vías),
  el binary_sensor, y la propagación de `Context`.

## Estado de los tests

Este entorno sandbox no tiene Python 3.14 (que es lo que pide
`homeassistant`, pinneado en `requirements_dev.txt` (2026.9.4 desde el 06/10/2026)). Python
3.13 sí está disponible, y con eso pip resuelve `homeassistant==2026.2.3`
(la última compatible con 3.13). Con esa versión corre bien:

```bash
python3.13 -m venv .venv-test
source .venv-test/bin/activate
pip install pytest-homeassistant-custom-component pytest-asyncio
pytest tests/ -q
```

**Resultado verificado en esta sesión: 109 passed, 11 skipped, 0 failed** — incluye
toda la suite preexistente más `test_fase1_fixes.py` y `test_manual_override.py`.
Los tests preexistentes de `test_non_switch_device.py` y `test_power_device.py`
necesitaron un ajuste menor: agregar `context=ANY` a las llamadas mockeadas de
`ServiceRegistry.async_call`/`EventBus.fire`, porque ahora `do_service_action`/
`do_event_action` siempre pasan un `Context` (ver Fase 2, atribución de historial).
Si en la PC usás Python 3.14 con la versión pinneada exacta, no debería haber
diferencias de comportamiento relevantes para estos tests.

### En la PC Windows del usuario

HA no corre nativo en Windows (`fcntl`). Usar el Ubuntu de WSL, que ya tiene `uv` y
`python3.14`. Copiar el repo a `~/so-test` (correr desde `/mnt/c` sobre OneDrive es lento):

```bash
cd ~/so-test && uv venv -p 3.14 .venv
uv pip install -p .venv homeassistant==2026.6.1 pytest-homeassistant-custom-component pytest-asyncio
.venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

Resultado (06/10/2026): 110 passed, 11 skipped tanto con 2026.6.1 como con
**2026.9.4** (la versión que corre el usuario en su HA). Para 2026.9 hubo que adaptar
2 tests: `input_number.async_set_value` ya no funciona (se usa el servicio
`input_number.set_value`), y el listener de estado ahora corre durante
`async_turn_on` (fijar `now` antes de encender). El plugin nuevo falla por
"Lingering timer" si un listener no se cancela al parar HA: el coordinator ahora
cancela `_unsub_events`/`_unsub_raz_override` en `EVENT_HOMEASSISTANT_STOP`.

## Ajustes tras la primera prueba real (06/10/2026)

El usuario copió la rama a su HA y no vio cambios. Diagnóstico y correcciones:

- **Card cacheada (seguía mostrando €)**: el recurso Lovelace tenía URL fija. Ahora
  `__init__.py` la registra como `...solar-optimizer-card.js?v=<mtime del archivo>` y
  actualiza la entrada existente, así cada cambio del JS fuerza recarga. Si aun así
  sigue el €, revisar si hay otro recurso duplicado de la card en
  Ajustes > Paneles > Recursos (p. ej. instalado por HACS/`/local/`).
- **Fase 1 punto 2 estaba incompleto**: `power_entity_id` solo existe en dispositivos
  de potencia variable y es la entidad de *comando* (number), no de medición. Nuevo
  campo opcional `measured_power_entity_id` (sensor, device_class power, W o kW) en
  ambos tipos de dispositivo. **Solo visual**: la card lo usa para el número, la
  barra y el gráfico de potencia (que ahora también aparece en on/off), con línea
  punteada en `power_max` para comparar presupuesto vs real. El algoritmo NO lo usa:
  el usuario quiere que siga usando su `power_max` como presupuesto.
- **Barra en 0 sin sensor medido**: bug preexistente en `ManagedDevice.__init__`
  (ternario invertido: un on/off ya encendido al arrancar quedaba con
  `current_power = power_min = -1` hasta el primer refresh). Corregido + test. La card
  además usa `power_max` como respaldo para on/off cuando no hay sensor medido.
- **Real > presupuesto**: la barra se pinta naranja (`--warning-color`), el número
  también, y una marca vertical indica dónde termina el presupuesto. El gráfico
  agranda la escala y la línea queda sobre la punteada del presupuesto.
- **Traducciones**: faltaba la etiqueta de `measured_power_entity_id` en el paso
  `device` (on/off). Nuevo `translations/es.json` (español neutro) y tabla `es` en la
  card; la card elige idioma con `translator(lang)` y cae a inglés por clave. Al
  agregar campos nuevos, actualizar strings.json + en/fr/it/es en **ambos** pasos
  (`device` y `powered_device`) de config y options.

## Pendiente / próximos pasos sugeridos

1. ~~Confirmar que la suite completa de tests pasa~~ — ya confirmado (109 passed,
   11 skipped, 0 failed) y ya pusheado a `origin/claude/solar-optimizer-improvements-6ynf06`.
2. **Esto es lo que falta ahora** (incluye los ajustes del 06/10): el usuario prueba todo a fondo en su instalación
   real de HA (dispositivo real con `power_entity_id`, override manual desde la
   card y desde el dispositivo subyacente, servicio `clear_override`, el
   binary_sensor de override, y que el historial/logbook muestre la atribución).
3. Cuando el usuario esté conforme, avisar para recién ahí abrir el Pull Request
   (explícitamente prohibido hacerlo antes, incluso si todo el código ya está
   pusheado a la rama).
4. ~~Fase 3~~ hecha en `claude/three-phase`. Falta: prueba real del usuario y, con
   su OK final explícito, PR (rama limpia, sin CLAUDE.md, apilada sobre #215 o
   después de que #215 se mergee).
5. ~~Fase 4~~ hecha en la misma rama. Falta prueba real del usuario.

## Fase 3: trifásico (implementada 06/10/2026)

Requisito del usuario: monofásico debe quedar EXACTAMENTE igual (fase = 1 siempre).
- Config central: `phase_mode` (`single_phase` por defecto / `three_phase`),
  `power_consumption_l1/l2/l3_entity_id` (neto por fase, negativo exportando; obligatorios
  en trifásico, validado en `config_flow.validate_input` -> error `phase_entity_required`),
  `battery_phase` (`1`/`2`/`3`/`all`, la fase del inversor híbrido).
- Dispositivo: `phase` (`1`/`2`/`3`/`all` = carga trifásica repartida 1/3 por fase).
  Ignorado en monofásico. Helper `phase_shares()` en `const.py`.
- Algoritmo: `_consommation_net` es un dict por fase (`{"1": neto}` en monofásico);
  el costo import/export se suma fase por fase. Parámetro nuevo opcional
  `phase_consumption` en `recuit_simule` (None = monofásico). La producción solar sigue
  global (no entra en el costo, solo display). El sensor global de consumo se mantiene.
- Coordinator: lee las 3 fases, suma la potencia de batería a `battery_phase`;
  `power_consumption_phases` en los datos -> atributos `l1/l2/l3` del sensor
  `power_consumption`. La card muestra neto por fase y badge de fase por dispositivo
  solo si existen esos atributos.
- Sin migración: todos los campos nuevos son opcionales con default.
- Verificación: el algoritmo nuevo da resultados idénticos al de `upstream/main` en 500
  escenarios monofásicos aleatorios con la misma semilla (script ad hoc, no commiteado).
  `tests/test_three_phase.py` (8 tests) falla si se anula el cálculo por fase.
  Suite: 121 passed, 11 skipped en HA 2026.6.1 y 2026.9.4.

## Fase 4: uso de batería por dispositivo (implementada 06/10/2026)

- Dispositivo: `battery_policy` = `battery_first` (solo excedente después de cargar la
  batería, nunca la descarga) / `load_first` (por defecto = comportamiento histórico:
  puede tomar la potencia de carga) / `use_battery` (además puede descargarla hasta su
  `battery_soc_threshold`).
- Central: `battery_max_discharge_power` (W, opcional; vacío = la descarga medida es el
  límite, o sea que con la batería ociosa `use_battery` no puede usarla).
- Regla del usuario: en trifásico, un dispositivo que no está en la fase de la batería
  ignora TODO lo de batería (política y umbral SOC): se le pasa `battery_soc=None` y su
  política se trata como `load_first`.
- Algoritmo: `appliquer_politique_batterie()` ajusta import/export solo en las fases de la
  batería: lo que `battery_first` le quita a la carga cuenta como import (y la carga que
  impide como export); `use_battery` puede cubrir su import con descarga hasta el límite.
  Con todos en `load_first` no cambia nada: verificado contra `upstream/main` en 500
  escenarios con potencia de batería y límite de descarga (resultados idénticos).
- SO no controla el inversor: la política decide cuándo prender cada dispositivo.
- La card no muestra la política (posible mejora).
- Tests: `tests/test_battery_policy.py` (10), verificados por mutación.
  Suite: 131 passed, 11 skipped en HA 2026.6.1 y 2026.9.4.
