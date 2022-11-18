import pathlib

DESIGN_RESULT = "/mnt/P41/Repositories/ProteinMPNN/AI_project/vanila_model/mRFP_SWISS_MODEL_vanila_design+targeted_sequence_design.fa"
TARGET_DIR = pathlib.Path(DESIGN_RESULT).parent / "separated"


if __name__ == "__main__":
    # Read the fasta file
    fasta = open(DESIGN_RESULT, "r")
    lines = fasta.readlines()
    fasta.close()

    # Write the fasta file
    pathlib.Path(TARGET_DIR).mkdir(parents=True, exist_ok=True)

    for i in range(0, len(lines), 2):
        id = lines[i].split(",")[0][1:]

        with open(f"{pathlib.Path(TARGET_DIR) / id}.fa", "wt") as fa:
            fa.write(lines[i])
            fa.write(lines[i + 1])

    print("Done")
