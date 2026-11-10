"""Three-phase support: each device is optimized against the net consumption of its
own phase, a three-phase load is split evenly on the 3 phases, and the battery
charging power is added to the phase(s) of its inverter."""

# pylint: disable=unused-argument, wildcard-import, unused-wildcard-import

import pytest

from homeassistant.data_entry_flow import FlowResultType

from custom_components.solar_optimizer import config_flow
from custom_components.solar_optimizer.coordinator import SolarOptimizerCoordinator

from .commons import *


CENTRAL_DATA = {
    CONF_NAME: "Configuration",
    CONF_REFRESH_PERIOD_SEC: 60,
    CONF_DEVICE_TYPE: CONF_DEVICE_CENTRAL,
    CONF_POWER_CONSUMPTION_ENTITY_ID: "sensor.fake_power_consumption",
    CONF_POWER_PRODUCTION_ENTITY_ID: "sensor.fake_power_production",
    CONF_SELL_COST_ENTITY_ID: "input_number.fake_sell_cost",
    CONF_BUY_COST_ENTITY_ID: "input_number.fake_buy_cost",
    CONF_SELL_TAX_PERCENT_ENTITY_ID: "input_number.fake_sell_tax_percent",
    CONF_SMOOTH_PRODUCTION: False,
    CONF_BATTERY_CHARGE_POWER_ENTITY_ID: "sensor.fake_battery_charge_power",
    CONF_RAZ_TIME: "05:00",
}

THREE_PHASE_DATA = {
    **CENTRAL_DATA,
    CONF_PHASE_MODE: CONF_PHASE_MODE_THREE,
    CONF_POWER_CONSUMPTION_L1_ENTITY_ID: "sensor.fake_power_l1",
    CONF_POWER_CONSUMPTION_L2_ENTITY_ID: "sensor.fake_power_l2",
    CONF_POWER_CONSUMPTION_L3_ENTITY_ID: "sensor.fake_power_l3",
}


def device_data(name, entity, power, phase):
    """An on/off device on the given phase"""
    return {
        CONF_NAME: name,
        CONF_DEVICE_TYPE: CONF_DEVICE,
        CONF_ENTITY_ID: entity,
        CONF_POWER_MAX: power,
        CONF_PHASE: phase,
        CONF_CHECK_USABLE_TEMPLATE: "{{ True }}",
        CONF_DURATION_MIN: 0.3,
        CONF_DURATION_STOP_MIN: 0.1,
        CONF_ACTION_MODE: CONF_ACTION_MODE_ACTION,
        CONF_ACTIVATION_SERVICE: "input_boolean/turn_on",
        CONF_DEACTIVATION_SERVICE: "input_boolean/turn_off",
    }


async def setup(hass, central_data, devices):
    """Create the central config and the devices, return the coordinator"""
    await create_managed_device(
        hass,
        MockConfigEntry(domain=DOMAIN, title="Central", unique_id="centralUniqueId", data=central_data),
        "centralUniqueId",
    )
    for name, entity, power, phase, *extra in devices:
        data = {**device_data(name, entity, power, phase), **(extra[0] if extra else {})}
        await create_managed_device(
            hass,
            MockConfigEntry(domain=DOMAIN, title=name, unique_id=name, data=data),
            name_to_unique_id(name),
        )
    return SolarOptimizerCoordinator.get_coordinator()


async def run(hass, coordinator, states: dict):
    """Run one optimization with the given sensor values, return {device name: state}"""
    side_effects = SideEffects(
        {
            entity_id: State(entity_id, value)
            for entity_id, value in {
                "sensor.fake_power_production": 5000,
                "sensor.fake_battery_charge_power": 0,
                "input_number.fake_sell_cost": 1,
                "input_number.fake_buy_cost": 1,
                "input_number.fake_sell_tax_percent": 0,
                **states,
            }.items()
        },
        State("unknown.entity_id", "unknown"),
    )
    with patch("homeassistant.core.StateMachine.get", side_effect=side_effects.get_side_effects()), patch("homeassistant.core.ServiceRegistry.async_call"):
        data = await coordinator._async_update_data()
        await hass.async_block_till_done()
    return data, {e["name"]: e["state"] for e in data["best_solution"]}


async def test_device_uses_its_own_phase_budget(hass: HomeAssistant, reset_coordinator):
    """L1 exports 1000 W while L2 imports: only the device on L1 is turned on, even though
    the global net (-500 W) would let the algorithm pick any of the two."""
    coordinator = await setup(
        hass,
        THREE_PHASE_DATA,
        [("Device L1", "input_boolean.fake_l1", 1000, "1"), ("Device L2", "input_boolean.fake_l2", 1000, "2")],
    )
    data, states = await run(
        hass,
        coordinator,
        {
            "sensor.fake_power_consumption": -500,
            "sensor.fake_power_l1": -1000,
            "sensor.fake_power_l2": 500,
            "sensor.fake_power_l3": 0,
        },
    )
    assert states == {"Device L1": True, "Device L2": False}
    assert data["power_consumption_phases"] == {"1": -1000, "2": 500, "3": 0}
    # real power left after the decision: L2 still imports 500 W, nothing exported
    assert data["power_mismatch"] == 500
    assert data["grid_import"] == 500 and data["grid_export"] == 0


@pytest.mark.parametrize(
    "l1, l2, l3, expected",
    [
        # each phase exports 1000 W: the load fits exactly
        (-1000, -1000, -1000, True),
        # on: L1 exports 2000 and L2/L3 import 1000 each (cost 2000) vs off: L1 exports 3000 (cost 1500)
        (-3000, 0, 0, False),
    ],
)
async def test_three_phase_load_split_on_all_phases(hass: HomeAssistant, reset_coordinator, l1, l2, l3, expected):
    """A 3000 W three-phase load takes 1000 W on each phase. The global net is -3000 W in
    both cases, so a global optimization would always turn it on."""
    coordinator = await setup(hass, THREE_PHASE_DATA, [("Heat pump", "input_boolean.fake_heat_pump", 3000, PHASE_ALL)])
    _, states = await run(
        hass,
        coordinator,
        {"sensor.fake_power_consumption": -3000, "sensor.fake_power_l1": l1, "sensor.fake_power_l2": l2, "sensor.fake_power_l3": l3},
    )
    assert states == {"Heat pump": expected}


@pytest.mark.parametrize("battery_phase, expected", [("2", {"Device L1": False, "Device L2": True}), ("1", {"Device L1": True, "Device L2": False})])
async def test_battery_power_added_to_its_phase(hass: HomeAssistant, reset_coordinator, battery_phase, expected):
    """1000 W charging the battery is surplus available on the phase of the inverter only"""
    coordinator = await setup(
        hass,
        {**THREE_PHASE_DATA, CONF_BATTERY_PHASE: battery_phase},
        [("Device L1", "input_boolean.fake_l1", 1000, "1"), ("Device L2", "input_boolean.fake_l2", 1000, "2")],
    )
    _, states = await run(
        hass,
        coordinator,
        {
            "sensor.fake_power_consumption": 0,
            "sensor.fake_battery_charge_power": -1000,
            "sensor.fake_power_l1": 0,
            "sensor.fake_power_l2": 0,
            "sensor.fake_power_l3": 0,
        },
    )
    assert states == expected


async def test_unknown_phase_value_abandons_calculation(hass: HomeAssistant, reset_coordinator):
    """Like an unknown global consumption, an unknown phase value stops the calculation"""
    coordinator = await setup(hass, THREE_PHASE_DATA, [("Device L1", "input_boolean.fake_l1", 1000, "1")])
    data, states = await run(hass, coordinator, {"sensor.fake_power_consumption": -1000, "sensor.fake_power_l1": -1000, "sensor.fake_power_l2": 0})
    assert states == {}
    assert data["best_objective"] == -1


async def test_single_phase_ignores_device_phase(hass: HomeAssistant, reset_coordinator):
    """Without three-phase mode, the device phase is ignored: the global net is used"""
    coordinator = await setup(hass, CENTRAL_DATA, [("Device L2", "input_boolean.fake_l2", 1000, "2")])
    data, states = await run(hass, coordinator, {"sensor.fake_power_consumption": -1000})
    assert states == {"Device L2": True}
    assert data["power_consumption_phases"] is None


async def test_config_flow_three_phase_requires_phase_entities(hass: HomeAssistant, skip_hass_states_get, reset_coordinator):
    """Three-phase mode without the 3 phase entities is refused"""
    result = await hass.config_entries.flow.async_init(config_flow.DOMAIN, context={"source": "user"})
    assert result["step_id"] == "device_central"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "power_consumption_entity_id": "input_number.power_consumption",
            "power_production_entity_id": "input_number.power_production",
            "sell_cost_entity_id": "input_number.sell_cost",
            "buy_cost_entity_id": "input_number.buy_cost",
            "sell_tax_percent_entity_id": "input_number.tax_percent",
            "phase_mode": CONF_PHASE_MODE_THREE,
            "power_consumption_l1_entity_id": "sensor.power_l1",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"power_consumption_l2_entity_id": "phase_entity_required"}
