# Wirkungsgradermittlung-Zendure-Ger-te
Provides Codes for tracking Efficiency while normal usage

Needs:
- Z-HA Intergration
- Ploty graph card from HACS
- APPDaemon from HACS
- eg Studio Code Server from HACS
- device for logging input/output (in my case, it`s a Shelly Plug)

Entities:
- mode_entity (Sensor which shows charging (-) and discharging (+))
- plug_entity (Sensor which shows input/output power device 1, in my case a Shelly Plug is installed)
- pack_p1_entity (Sensor which which shows input/output power of the battery; IMPORTANT: have to be a batterie entity, not an inverter entity;can be more then 1 pack)

How to be set:
- install AppDaemon from HACS
- install Ploty graph card from HACS
- install and setup Z-HA integration from HACS
- install Studio Code Server from HACS
- insert your entities in wg_curve_dual_mode.py
- open Studio Code Server and search for the AppDaemon path (possibly a container)
- copy wg_curve_dual_mode.py to .../appdaemon/apps/
- copy content from apps.yaml to  .../appdaemon/apps/apps.yaml
- open Home Assistant dashboard
- insert new card (Ploty graph card)
- open the card - open the code editor and insert the code from "ploty graph card"
- after a couple of minutes, the first values should be visible in the ploty graph card

  <img width="473" height="351" alt="image" src="https://github.com/user-attachments/assets/9184cf76-60a9-4d99-803b-b0ddc13f75c4" />

