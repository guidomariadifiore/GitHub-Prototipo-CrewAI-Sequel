import os

# Imposta qui il percorso assoluto della cartella che contiene i tuoi progetti Java da analizzare
# Esempio Windows: "C:\\Users\\Nome\\Documents\\JavaProjects"
# Esempio Mac/Linux: "/home/user/java_projects"

DIRECTORY_REPOS = "C:\\Users\\lampa\\Documents\\GitHub"

# Header standard se servono per chiamate HTTP future
HEADER = {
    "Content-Type": "application/x-www-form-urlencoded"
}

# La metrica non serve più come costante fissa perché usiamo la ricerca issues generica, 
# ma la lasciamo per compatibilità se servisse
METRIC_TO_REFACTOR = "energy_smells"