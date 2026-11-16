# Déploiement du portfolio : de Git vers FTP

Script Python qui met en ligne mon portfolio sur un serveur FTP à partir de son dépôt Git.

**Débutant ? Commence par le [guide pas à pas](docs/guide-pas-a-pas.md)** : il explique chaque étape et chaque commande pour refaire l’exercice depuis zéro.

- **Auteur :** Olivier Pizelle, BTS SIO option SISR, MyDigitalSchool Nantes
- **Dépôt source :** https://github.com/n7rpvygk44-gif/portfolio-dashboard
- **Serveur de test :** hébergement Infomaniak (FTP), dossier `/portfolio`

## Ce que fait le script

| Étape | Rôle | Commandes utilisées |
|---|---|---|
| 1. Git | Récupère la dernière version du portfolio sur l'ordinateur : `git clone` si le dossier n'existe pas, sinon `git pull`. | `git clone`, `git pull --ff-only` |
| 2. Tri (facultatif de l'exercice) | Liste les fichiers modifiés depuis le dernier déploiement pour n'envoyer que ceux-là. La première fois, tout est envoyé. | `git diff --name-status`, `git ls-files` |
| 3. Connexion | Se connecte au serveur en **FTPS** (FTP chiffré par TLS) : le mot de passe et les fichiers ne circulent pas en clair. | `AUTH TLS`, `USER`/`PASS`, `PROT P` |
| 4. Envoi | Envoie les fichiers, supprime sur le serveur ceux effacés dans Git, puis note le commit déployé dans `etat-deploiement.json`. | `MKD`, `STOR`, `DELE`, `QUIT` |

Règles de sécurité :

- **Aucun mot de passe dans le code.** Il est demandé au lancement (saisie masquée), ou lu dans `config.ini`, que `.gitignore` empêche de publier.
- **Fichiers exclus :** `.git`, `.gitignore`, `.DS_Store`, `README.md`, `docs/`… (liste réglable dans `config.ini`).
- **Contenu en ligne protégé :** les fichiers `data/*.json` sont modifiés depuis le backoffice `/admin`. Le script ne les envoie que s'ils n'existent pas encore sur le serveur, pour ne jamais écraser ce qui a été saisi en ligne.
- **Version exacte de Git :** le script refuse de partir si le dépôt local contient des modifications sans commit, ou s'il n'est pas sur la branche `main`. Les fichiers envoyés sont lus dans le commit lui-même (`git show`).
- **Reprise après erreur :** le commit n'est noté qu'à la fin d'un envoi réussi. Si la connexion coupe, il suffit de relancer.

## Installation (Mac)

Il faut seulement Python 3 et Git, déjà présents sur macOS avec les outils de développement. Vérifier dans le Terminal :

```bash
python3 --version   # affiche la version de Python (3.8 ou plus)
git --version       # affiche la version de Git
```

Si une des deux commandes propose d'installer les « outils de ligne de commande », accepter. Aucune bibliothèque à installer avec `pip` : le script n'utilise que la bibliothèque standard de Python.

## Configuration

```bash
cd ~/Documents/deploiement-ftp       # aller dans le dossier du script
cp config.exemple.ini config.ini     # copier le modèle de configuration
open -e config.ini                   # l'ouvrir dans TextEdit pour le relire
```

Les réglages importants de `config.ini` :

- `[git]` : adresse du dépôt, branche (`main`), dossier local (`~/Documents/portfolio-dashboard`).
- `[ftp]` : hôte, identifiant, dossier distant (`/portfolio`), `chiffrement = oui`. Laisser `mot_de_passe` vide pour qu'il soit demandé à chaque fois.
- `[envoi]` : fichiers à exclure, fichiers à protéger, suppression des fichiers effacés.

## Utilisation

```bash
python3 deployer.py --essai   # 1re fois : montre ce qui serait fait, sans rien modifier
python3 deployer.py           # déploiement réel (seulement les changements)
python3 deployer.py --tout    # renvoie tous les fichiers (après un souci sur le serveur)
python3 deployer.py --detail  # affiche le dialogue FTP complet (dépannage)
```

Le mot de passe FTP est demandé ; rien ne s'affiche pendant la frappe, c'est normal.

Exemple de sortie après une modification du site :

```
2. Recherche des fichiers à envoyer
Changements depuis le dernier déploiement (4ffbf13 → a7c24b3) :
  D  bin/router.php
  M  data/kb.json
  M  public/assets/css/admin.css
  A  public/nouveau.txt
À envoyer : 3 fichier(s), à supprimer : 1.
...
  protégé, laissé tel quel : data/kb.json
  envoi : public/assets/css/admin.css (5386 octets)... ok
  envoi : public/nouveau.txt (8 octets)... ok
  supprimé : bin/router.php
Bilan : 2 envoyé(s), 1 supprimé(s), 1 protégé(s).
```

## Fichiers du dossier

| Fichier | Rôle | Publié sur GitHub ? |
|---|---|---|
| `deployer.py` | Le script, commenté étape par étape | Oui |
| `config.exemple.ini` | Modèle de configuration, sans mot de passe | Oui |
| `.gitignore` | Empêche de publier `config.ini` et l'état local | Oui |
| `config.ini` | Ma configuration réelle | **Non** |
| `etat-deploiement.json` | Dernier commit déployé sur chaque serveur | Non |

## Dépannage

| Message | Cause probable | Solution |
|---|---|---|
| `connexion refusée par le serveur : 530` | Identifiant ou mot de passe faux | Vérifier dans le Manager Infomaniak › FTP |
| `impossible de joindre … : timed out` | Réseau, ou FTPS bloqué | Réessayer ; en dernier recours `chiffrement = non` (non chiffré) |
| `l'envoi a été interrompu : timed out` | Connexion de données FTPS bloquée (corrigé le 16/11/2026) | Mettre le script à jour ; sinon `--detail` pour voir où ça bloque |
| `certificate verify failed` | Certificat du serveur non reconnu | Sur Mac, lancer « Install Certificates.command » dans le dossier Python des Applications |
| `le dépôt local est sur la branche …` | GitHub Desktop est sur une autre branche | Repasser sur `main` dans GitHub Desktop |
| `modifications non enregistrées` | Des fichiers ont été modifiés sans commit | Faire un commit (ou annuler) dans GitHub Desktop |

## Compétences mises en œuvre (épreuve E5)

- Mettre à disposition un service informatique : déploiement automatisé d'un site web sur un hébergeur.
- Gérer le patrimoine informatique : gestion de versions avec Git, suivi des changements par commit.
- Sécurité : transfert chiffré (FTPS/TLS), aucun secret dans le code ni dans Git, protection des données de production.
- Développement d'un script d'administration en Python (bibliothèque standard : `ftplib`, `subprocess`, `configparser`).
