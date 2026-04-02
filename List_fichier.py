import os

# Dossier à explorer
folder_path = r"C:\Program Files\Princeton Instruments\PICam\Runtime"

# Fichier de sortie
output_file = "picam_runtime_files.txt"

with open(output_file, "w", encoding="utf-8") as f:
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            full_path = os.path.join(root, file)
            f.write(full_path + "\n")

print(f"Liste des fichiers enregistrée dans : {output_file}")