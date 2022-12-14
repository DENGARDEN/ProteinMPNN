import os
import glob
import pathlib
import json
import shutil

MODE = "full"
TARGET_SEARCH_DIR = (
    f"/mnt/P41/Repositories/ProteinMPNN/AI_project/vanila_model/AlphaFold/{MODE}/"
)
OUTPUT_DIR = (
    f"/mnt/P41/Repositories/ProteinMPNN/AI_project/vanila_model/AlphaFold/{MODE}/plddt/"
)
# JSON listing
# Search recursively

pathlib.Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
for filename in sorted(pathlib.Path(TARGET_SEARCH_DIR).rglob("ranking_debug.json")):
    p = os.path.abspath(filename)

    # DEBUG
    # print(p)
    # print(pathlib.Path(p).parent.name)

    sample_name = pathlib.Path(p).parent.name
    # Read JSON
    with open(p) as j:
        data = json.load(j)

        # DEV
        # print(data["order"][0])

        target_pkl = f"{pathlib.Path(p).parent}/result_{data['order'][0]}.pkl"

        shutil.copy(target_pkl, f"{OUTPUT_DIR}/{sample_name}.pkl")

# Pick best prediction

# then copy it
