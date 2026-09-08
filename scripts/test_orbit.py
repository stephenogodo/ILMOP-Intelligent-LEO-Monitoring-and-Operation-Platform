from services.satellite_simulator.orbit import OrbitModel

orbit = OrbitModel()

lat, lon, alt = orbit.propagate()

print(f"Latitude : {lat:.2f}")
print(f"Longitude: {lon:.2f}")
print(f"Altitude : {alt:.2f}")