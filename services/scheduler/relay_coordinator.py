# At each AOS event, queries the ISL visibility model for dark satellites reachable from the contact satellite.
#Computes the available relay bandwidth (ISL link budget) and the remaining contact window duration.
#Prioritises which dark satellites to collect from mlflow import data
#from — by queue depth, data age, or priority flag.
#Issues collect requests and tracks acknowledgements.
#Reports total relayed volume per contact window to the dashboard.#