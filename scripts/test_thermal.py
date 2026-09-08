from services.satellite_simulator.thermal import ThermalModel

thermal = ThermalModel()

for i in range(10):
    temp = thermal.update()

    print(
        f"{i+1}: Temperature={temp:.2f}°C"
    )