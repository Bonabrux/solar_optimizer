"""Battery usage policy per device: battery_first only uses the surplus left after
charging the battery, load_first (default, historical behavior) may take the
charging power, use_battery may also discharge the battery. In three-phase, a device
on a phase without the battery ignores the battery entirely."""

# pylint: disable=unused-argument, wildcard-import, unused-wildcard-import

import pytest

from .commons import *
from .test_three_phase import setup, run, CENTRAL_DATA, THREE_PHASE_DATA

# imported kWh 3 times more expensive than exported: a device should not import to avoid exporting
EXPENSIVE_IMPORT = {"input_number.fake_buy_cost": 3, "input_number.fake_sell_cost": 1}


@pytest.mark.parametrize(
    "policy, grid, expected",
    [
        # the battery absorbs 1000 W and nothing is exported
        (BATTERY_POLICY_BATTERY_FIRST, 0, False),
        (BATTERY_POLICY_LOAD_FIRST, 0, True),
        # 1000 W are exported on top of the battery charging: enough for both policies
        (BATTERY_POLICY_BATTERY_FIRST, -1000, True),
        (BATTERY_POLICY_LOAD_FIRST, -1000, True),
    ],
)
async def test_battery_first_does_not_take_charging_power(hass: HomeAssistant, reset_coordinator, policy, grid, expected):
    """1000 W are charging the battery"""
    coordinator = await setup(hass, CENTRAL_DATA, [("Pool", "input_boolean.fake_pool", 1000, "1", {CONF_BATTERY_POLICY: policy})])
    # grid net from the meter; the coordinator adds the battery charging power to it
    _, states = await run(hass, coordinator, {"sensor.fake_power_consumption": grid, "sensor.fake_battery_charge_power": -1000})
    assert states == {"Pool": expected}


@pytest.mark.parametrize(
    "policy, max_discharge, expected",
    [
        # 600 W exported, the 400 W missing would be imported: not worth it
        (BATTERY_POLICY_LOAD_FIRST, 2000, False),
        # the battery can cover the 400 W missing
        (BATTERY_POLICY_USE_BATTERY, 2000, True),
        # but not if it can only give 100 W
        (BATTERY_POLICY_USE_BATTERY, 100, False),
    ],
)
async def test_use_battery_covers_the_gap(hass: HomeAssistant, reset_coordinator, policy, max_discharge, expected):
    """A 1000 W device with 600 W of surplus"""
    coordinator = await setup(
        hass,
        {**CENTRAL_DATA, CONF_BATTERY_MAX_DISCHARGE_POWER: max_discharge},
        [("Heat pump", "input_boolean.fake_heat_pump", 1000, "1", {CONF_BATTERY_POLICY: policy})],
    )
    _, states = await run(hass, coordinator, {"sensor.fake_power_consumption": -600, **EXPENSIVE_IMPORT})
    assert states == {"Heat pump": expected}


async def test_use_battery_without_max_uses_measured_discharge(hass: HomeAssistant, reset_coordinator):
    """Without max discharge power, the measured discharge is the limit: the battery is
    idle so it cannot cover the gap"""
    coordinator = await setup(
        hass, CENTRAL_DATA, [("Heat pump", "input_boolean.fake_heat_pump", 1000, "1", {CONF_BATTERY_POLICY: BATTERY_POLICY_USE_BATTERY})]
    )
    _, states = await run(hass, coordinator, {"sensor.fake_power_consumption": -600, **EXPENSIVE_IMPORT})
    assert states == {"Heat pump": False}


async def test_three_phase_device_without_battery_ignores_battery(hass: HomeAssistant, reset_coordinator):
    """Battery on L1 at 10% SOC: the device on L1 is below its 50% threshold, the device
    on L2 ignores the threshold and its battery_first policy, and uses the L2 surplus"""
    coordinator = await setup(
        hass,
        {**THREE_PHASE_DATA, CONF_BATTERY_PHASE: "1", CONF_BATTERY_SOC_ENTITY_ID: "sensor.fake_battery_soc"},
        [
            ("Device L1", "input_boolean.fake_l1", 1000, "1", {CONF_BATTERY_SOC_THRESHOLD: 50}),
            ("Device L2", "input_boolean.fake_l2", 1000, "2", {CONF_BATTERY_SOC_THRESHOLD: 50, CONF_BATTERY_POLICY: BATTERY_POLICY_BATTERY_FIRST}),
        ],
    )
    _, states = await run(
        hass,
        coordinator,
        {
            "sensor.fake_battery_soc": 10,
            "sensor.fake_power_consumption": -2000,
            "sensor.fake_power_l1": -1000,
            "sensor.fake_power_l2": -1000,
            "sensor.fake_power_l3": 0,
        },
    )
    assert states == {"Device L1": False, "Device L2": True}
    assert coordinator.get_device_by_name("Device L2").battery_soc is None


async def test_three_phase_battery_first_on_battery_phase(hass: HomeAssistant, reset_coordinator):
    """Battery on L1 charging 1000 W, no export: battery_first on L1 stays off"""
    coordinator = await setup(
        hass,
        {**THREE_PHASE_DATA, CONF_BATTERY_PHASE: "1"},
        [("Device L1", "input_boolean.fake_l1", 1000, "1", {CONF_BATTERY_POLICY: BATTERY_POLICY_BATTERY_FIRST})],
    )
    _, states = await run(
        hass,
        coordinator,
        {
            "sensor.fake_battery_charge_power": -1000,
            "sensor.fake_power_consumption": 0,
            "sensor.fake_power_l1": 0,
            "sensor.fake_power_l2": 0,
            "sensor.fake_power_l3": 0,
        },
    )
    assert states == {"Device L1": False}


async def test_power_mismatch_does_not_count_battery_charging_as_export(hass: HomeAssistant, reset_coordinator):
    """1000 W go into the battery and 300 W to the grid: a battery_first device of 1000 W
    stays off, and the real mismatch is the 300 W exported, not 1300 W"""
    coordinator = await setup(hass, CENTRAL_DATA, [("Pool", "input_boolean.fake_pool", 1000, "1", {CONF_BATTERY_POLICY: BATTERY_POLICY_BATTERY_FIRST})])
    data, states = await run(hass, coordinator, {"sensor.fake_power_consumption": -300, "sensor.fake_battery_charge_power": -1000})
    assert states == {"Pool": False}
    assert data["power_mismatch"] == 300
    assert data["grid_export"] == 300 and data["grid_import"] == 0

