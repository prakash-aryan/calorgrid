Things to resolve:
1. Mismatched hardware. The hardware price is a 2020 server with eight A100 GPUs, the power draw is from a 2025 H100 system, and the market price is for a V100. We are comparing the cost of one machine with the rental price of another.
2. Costs we leave out. There is no building, network, staff, installation, idle power or profit margin in our number. A cloud provider's price has to cover all of those.
3. Maintenance is a guess. The 5% a year has no source behind it.

New Additions:
1. I want to provide a measure when the GPU requests comes in the following tiers - 
Kind	What the buyer gets	Typical price against on-demand
On-demand	Start and stop any time, not interrupted	Full price
Spot / preemptible	Spare capacity the vendor can take back at short notice	Roughly 60–90% cheaper; your 2020 V100 figures are 0.92 against 3.06 USD
Reserved / committed use	A 1- or 3-year commitment to a set amount	Roughly 30–70% cheaper, depending on term
Capacity blocks / scheduled reservations	A fixed GPU cluster booked for a set window of days or weeks	Around or somewhat below on-demand
Dedicated hosts / bare metal	A whole physical machine for one client	At or above on-demand
Serverless / per-request	Pay per second of inference or per token, no machine to manage	Higher per hour, but nothing paid while idle


2. Need to consider the new electricity and heat price model provided in context/house_heat_details
   Done: backup heat is priced from the gas bills (scripts/gas_price.py, data/gas_price.json; 0.1355 CHF/kWh in 2025 ÷ boiler efficiency 0.90). The dashboard defaults to gas and can switch to the heat pump. The Strom files have no prices, so server electricity stays at the ElCom tariff.