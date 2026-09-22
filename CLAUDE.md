# Solar Optimizer — contexto de trabajo con Claude

Este archivo resume el estado de una serie de mejoras que se están implementando
sobre esta integración de Home Assistant (fork personal de jmcollin78/solar_optimizer),
para poder retomar el trabajo con Claude desde otra sesión/dispositivo sin perder contexto.

Repo: `bonabrux/solar_optimizer`
Rama de trabajo: `claude/solar-optimizer-improvements-6ynf06`
No se debe abrir Pull Request hasta que el usuario lo pida explícitamente — probar
todo a fondo primero. Todo el trabajo se pushea a esa rama en el fork.

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
- **Fase 3** (soporte trifásico) — **NO empezada**. Requiere diseño previo:
  selector monofásico/trifásico en config central, entidades de consumo/producción
  por fase, atributo `phase` por dispositivo, y adaptar
  `simulated_annealing_algo.py` para manejar 3 presupuestos en vez de uno.
- **Fase 4** (batería con prioridad de despacho) — **NO empezada**. Depende del
  diseño de Fase 3 para ser preciso (inversor híbrido monofásico del usuario está
  en una fase específica).

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
`homeassistant==2026.6.1`, la versión pinneada en `requirements_dev.txt`). Python
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

## Pendiente / próximos pasos sugeridos

1. Confirmar que la suite completa de tests pasa (ver arriba).
2. Probar a fondo en una instalación real de HA antes de pedir el PR.
3. Cuando el usuario esté conforme, avisar para recién ahí abrir el Pull Request
   (explícitamente prohibido hacerlo antes).
4. Diseñar Fase 3 (trifásico) en detalle antes de tocar código: nombres de campos
   de config, migración de configs existentes con `config_flow.py`/`CONFIG_VERSION`.
5. Fase 4 (batería) después de Fase 3.
