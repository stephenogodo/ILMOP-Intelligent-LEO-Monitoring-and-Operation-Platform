from services.satellite_simulator.battery import BatteryModel

battery = BatteryModel()

for i in range(10):
    pct, voltage = battery.update()

    print(
        f"{i+1}: "
        f"Battery={pct:.2f}% "
        f"Voltage={voltage:.2f}V"
    )