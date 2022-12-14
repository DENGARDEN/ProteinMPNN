# run multiple fa on alphafold automatically
import glob
import subprocess
import shlex
import sys
import pathlib

""" 
python3 docker/run_docker.py --fasta_paths=./protein_sequence/REC3.fasta --max_template_date=2021-11-01  --model_preset=monomer --data_dir=/mnt/md0/alphafold_DB/
"""
python = "/home/dengarden/anaconda3/envs/mlfold_v3.9/bin/python"
alphafold_script_dir = "/mnt/P41/Repositories/alphafold/docker/run_docker.py"
alphafold_db_dir = "/mnt/md0/alphafold_DB/"

# custom inputs ##########
MODE = "full"
fasta_path = (
    pathlib.Path(
        "/mnt/P41/Repositories/ProteinMPNN/AI_project/fixed_network_model[TYPE2]/separated/"
    )
    / f"{MODE}"
)
output_dir = fasta_path.parent.parent / "AlphaFold" / f"{MODE}"
max_template_date = "2021-11-01"
model_preset = "monomer"


# print(glob.glob(fasta_path + "*.fa"))

for fasta in sorted(fasta_path.glob("*.fa")):
    # print(fasta)
    pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)

    cmd = f"{python} {alphafold_script_dir} --fasta_paths={fasta} --max_template_date={max_template_date} --model_preset={model_preset} --data_dir={alphafold_db_dir} --output_dir={output_dir} "
    print(cmd)
    # p = subprocess.Popen(['ls'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    p = subprocess.Popen(
        shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    for c in iter(lambda: p.stdout.read(1), b""):
        # TODO : https://stackoverflow.com/questions/60106146/catching-logger-info-output-in-python-subprocess
        sys.stdout.buffer.write(c)
