#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deployer.py : met en ligne le portfolio sur un serveur FTP à partir de Git.

Ce que fait le script, dans l'ordre :
  1. GIT  : récupère la dernière version du portfolio sur l'ordinateur
            (git clone si le dossier n'existe pas encore, sinon git pull).
  2. TRI  : cherche les fichiers modifiés depuis le dernier déploiement
            (partie facultative de l'exercice). La première fois, tout est envoyé.
  3. FTP  : se connecte au serveur FTP, en chiffré (FTPS) si possible.
  4. ENVOI: envoie les fichiers modifiés, supprime ceux effacés dans Git,
            puis note le commit déployé pour la prochaine fois.

Utilisation (dans le Terminal, depuis le dossier du script) :
  python3 deployer.py            déploiement normal (seulement les changements)
  python3 deployer.py --essai    montre ce qui serait fait, sans rien envoyer
  python3 deployer.py --tout     renvoie tous les fichiers

Le script n'utilise que la bibliothèque standard de Python et la commande git :
aucune installation avec pip n'est nécessaire.
"""

# ---------------------------------------------------------------------------
# Les « import » chargent des outils fournis avec Python.
# ---------------------------------------------------------------------------
import argparse      # lire les options passées au script (--essai, --tout...)
import configparser  # lire le fichier de configuration config.ini
import fnmatch       # comparer un nom de fichier à un motif comme "*.md"
import ftplib        # parler à un serveur FTP
import getpass       # demander le mot de passe sans l'afficher à l'écran
import io            # transformer des octets en "fichier" pour l'envoi FTP
import json          # lire et écrire le fichier d'état (format JSON)
import os            # lire les variables d'environnement
import ssl           # le chiffrement TLS utilisé par FTPS
import subprocess    # lancer la commande git depuis Python
import sys           # quitter le script avec un code d'erreur
from datetime import datetime
from pathlib import Path  # manipuler les chemins de fichiers facilement

# Dossier où se trouve ce script : config.ini et le fichier d'état y sont rangés.
DOSSIER_SCRIPT = Path(__file__).resolve().parent
FICHIER_ETAT = DOSSIER_SCRIPT / "etat-deploiement.json"


# ===========================================================================
# Petites fonctions d'affichage
# ===========================================================================
def titre(texte):
    """Affiche un titre d'étape bien visible."""
    print()
    print("=" * 60)
    print(texte)
    print("=" * 60)


def arreter(message):
    """Affiche une erreur compréhensible puis arrête le script."""
    print()
    print("ERREUR : " + message)
    sys.exit(1)


# ===========================================================================
# Lecture de la configuration
# ===========================================================================
def lire_configuration(chemin):
    """
    Lit config.ini et renvoie un dictionnaire avec tous les réglages.
    Le mot de passe n'est jamais écrit dans le code : il vient de config.ini
    (fichier ignoré par Git), d'une variable d'environnement, ou il est demandé.
    """
    if not chemin.exists():
        arreter(
            "le fichier de configuration " + str(chemin) + " est introuvable.\n"
            "Copie config.exemple.ini en config.ini puis remplis-le."
        )

    ini = configparser.ConfigParser()
    # On accepte aussi « oui » / « non » en plus de yes/no, true/false, 1/0.
    ini.BOOLEAN_STATES = dict(ini.BOOLEAN_STATES, oui=True, non=False)
    ini.read(chemin, encoding="utf-8")

    reglages = {
        # Partie [git]
        "depot": ini.get("git", "depot"),
        "branche": ini.get("git", "branche", fallback="main"),
        # expanduser transforme "~" en dossier personnel (/Users/olivier)
        "dossier_local": Path(ini.get("git", "dossier_local")).expanduser(),
        # Partie [ftp]
        "hote": ini.get("ftp", "hote"),
        "port": ini.getint("ftp", "port", fallback=21),
        "utilisateur": ini.get("ftp", "utilisateur"),
        "mot_de_passe": ini.get("ftp", "mot_de_passe", fallback=""),
        "dossier_distant": ini.get("ftp", "dossier_distant", fallback="/").rstrip("/"),
        "chiffrement": ini.getboolean("ftp", "chiffrement", fallback=True),
        # Secondes d'attente maximum d'une réponse du serveur avant « timed out ».
        "delai": ini.getint("ftp", "delai", fallback=60),
        # Partie [envoi] : listes de motifs, un par ligne
        "exclure": _liste(ini.get("envoi", "exclure", fallback="")),
        "proteger": _liste(ini.get("envoi", "proteger", fallback="")),
        "supprimer": ini.getboolean("envoi", "supprimer_fichiers_effaces", fallback=True),
        # Partie [racine] : .htaccess à placer à la racine du site (vide = rien)
        "htaccess_local": ini.get("racine", "htaccess", fallback="").strip(),
        "htaccess_distant": ini.get("racine", "dossier", fallback="/").rstrip("/") + "/.htaccess",
    }

    # Ordre de priorité pour le mot de passe :
    # 1) variable d'environnement FTP_MOT_DE_PASSE, 2) config.ini, 3) on le demande.
    reglages["mot_de_passe"] = os.environ.get("FTP_MOT_DE_PASSE") or reglages["mot_de_passe"]
    return reglages


def _liste(texte):
    """Transforme un texte sur plusieurs lignes en liste (lignes vides ignorées)."""
    return [ligne.strip() for ligne in texte.splitlines() if ligne.strip()]


# ===========================================================================
# ÉTAPE 1 : Git
# ===========================================================================
def git(dossier, *arguments):
    """
    Lance une commande git dans le dossier donné et renvoie ce qu'elle affiche.
    Exemple : git(dossier, "rev-parse", "HEAD") lance « git rev-parse HEAD ».
    """
    commande = ["git"] + list(arguments)
    try:
        resultat = subprocess.run(
            commande, cwd=str(dossier), capture_output=True, text=True, check=True
        )
    except FileNotFoundError:
        arreter("la commande git est introuvable. Sur Mac : xcode-select --install")
    except subprocess.CalledProcessError as erreur:
        arreter("la commande « " + " ".join(commande) + " » a échoué :\n" + erreur.stderr.strip())
    return resultat.stdout.strip()


def recuperer_depot(reglages):
    """git clone si le dossier n'existe pas, sinon git pull. Renvoie le commit obtenu."""
    titre("1. Récupération du portfolio depuis Git")
    dossier = reglages["dossier_local"]
    branche = reglages["branche"]

    if not (dossier / ".git").exists():
        # Le dépôt n'est pas encore sur l'ordinateur : on le télécharge entièrement.
        print("Le dossier " + str(dossier) + " n'existe pas : git clone...")
        dossier.parent.mkdir(parents=True, exist_ok=True)
        git(dossier.parent, "clone", "--branch", branche, reglages["depot"], str(dossier))
    else:
        # Le dépôt existe déjà. Avant de le mettre à jour, deux vérifications.
        # a) On doit être sur la bonne branche (GitHub Desktop peut en avoir changé).
        branche_actuelle = git(dossier, "rev-parse", "--abbrev-ref", "HEAD")
        if branche_actuelle != branche:
            arreter(
                "le dépôt local est sur la branche « " + branche_actuelle + " » au lieu de « "
                + branche + " ».\nDans GitHub Desktop, choisis la branche " + branche + " puis relance."
            )
        # b) Pas de modifications non enregistrées : sinon on enverrait un mélange.
        if git(dossier, "status", "--porcelain", "--untracked-files=no"):
            arreter(
                "le dépôt local contient des modifications non enregistrées (commit).\n"
                "Fais un commit ou annule-les dans GitHub Desktop, puis relance."
            )
        print("Le dépôt existe déjà : git pull...")
        # --ff-only : on accepte seulement une mise à jour simple, jamais une fusion.
        git(dossier, "pull", "--ff-only", "origin", branche)

    commit = git(dossier, "rev-parse", "HEAD")
    message = git(dossier, "log", "-1", "--format=%s")
    print("Version obtenue : " + commit[:7] + " « " + message + " »")
    return commit


# ===========================================================================
# ÉTAPE 2 : quels fichiers envoyer ?
# ===========================================================================
def lire_etat():
    """Lit le fichier qui mémorise le dernier commit déployé sur chaque serveur."""
    if FICHIER_ETAT.exists():
        return json.loads(FICHIER_ETAT.read_text(encoding="utf-8"))
    return {}


def cle_serveur(reglages):
    """Identifiant d'un serveur dans le fichier d'état, ex. ftp.exemple.com/portfolio."""
    return reglages["hote"] + reglages["dossier_distant"]


def est_exclu(chemin, motifs):
    """
    Vrai si le chemin correspond à un des motifs.
    "docs/*" exclut tout le dossier docs ; ".DS_Store" exclut ce fichier
    dans n'importe quel dossier (on compare aussi le simple nom du fichier).
    """
    nom = chemin.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(chemin, m) or fnmatch.fnmatch(nom, m) for m in motifs)


def lister_changements(reglages, commit_actuel, tout_envoyer):
    """
    Renvoie deux listes : (fichiers à envoyer, fichiers à supprimer sur le serveur).
    - Premier déploiement ou option --tout : tous les fichiers suivis par Git.
    - Sinon : seulement ce qui a changé entre le dernier commit déployé et maintenant.
    """
    titre("2. Recherche des fichiers à envoyer")
    dossier = reglages["dossier_local"]
    dernier = lire_etat().get(cle_serveur(reglages), {}).get("commit")

    # Le commit noté existe-t-il encore dans l'historique ? (sinon : tout renvoyer)
    if dernier and not tout_envoyer:
        try:
            subprocess.run(
                ["git", "cat-file", "-e", dernier + "^{commit}"],
                cwd=str(dossier), check=True, capture_output=True,
            )
        except subprocess.CalledProcessError:
            print("Le dernier commit déployé (" + dernier[:7] + ") est introuvable : envoi complet.")
            dernier = None

    a_envoyer, a_supprimer = [], []

    if tout_envoyer or not dernier:
        print("Déploiement complet : tous les fichiers suivis par Git.")
        # git ls-files : liste de tous les fichiers enregistrés dans Git.
        a_envoyer = git(dossier, "ls-files").splitlines()
    elif dernier == commit_actuel:
        print("Rien de nouveau depuis le dernier déploiement (" + dernier[:7] + ").")
    else:
        print("Changements depuis le dernier déploiement (" + dernier[:7] + " → " + commit_actuel[:7] + ") :")
        # git diff --name-status : une ligne par fichier, précédée d'une lettre.
        #   A = ajouté, M = modifié, D = supprimé (T = type changé)
        # --no-renames : un fichier renommé apparaît comme « supprimé + ajouté ».
        sortie = git(dossier, "diff", "--name-status", "--no-renames", dernier, commit_actuel)
        for ligne in sortie.splitlines():
            lettre, chemin = ligne.split("\t", 1)
            print("  " + lettre + "  " + chemin)
            if lettre == "D":
                a_supprimer.append(chemin)
            else:
                a_envoyer.append(chemin)

    # On retire les fichiers qui ne doivent jamais partir sur le serveur.
    exclus = [c for c in a_envoyer + a_supprimer if est_exclu(c, reglages["exclure"])]
    a_envoyer = [c for c in a_envoyer if c not in exclus]
    a_supprimer = [c for c in a_supprimer if c not in exclus]
    if exclus:
        print(str(len(exclus)) + " fichier(s) ignoré(s) (règle « exclure » de config.ini).")

    print("À envoyer : " + str(len(a_envoyer)) + " fichier(s), à supprimer : " + str(len(a_supprimer)) + ".")
    return a_envoyer, a_supprimer


# ===========================================================================
# ÉTAPE 3 : connexion FTP
# ===========================================================================
class FTPSAvecReprise(ftplib.FTP_TLS):
    """
    Version de FTP_TLS adaptée aux serveurs FTPS d'hébergeurs (Infomaniak...).

    En FTP, il y a DEUX connexions : la connexion de commande (login, MKD...)
    et une connexion de données ouverte pour chaque fichier transféré.
    Deux réglages évitent que la connexion de données reste bloquée (« timed out ») :
      1. on réutilise la session TLS de la connexion de commande pour les données ;
         beaucoup de serveurs l'exigent (« 522 session reuse required ») ;
      2. après l'envoi, on ferme la connexion de données sans attendre une réponse
         TLS que certains serveurs n'envoient jamais.
    """

    def ntransfercmd(self, cmd, rest=None):
        conn, taille = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host, session=self.sock.session
            )
        return conn, taille

    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
        """Comme ftplib, mais sans conn.unwrap(), qui peut attendre indéfiniment."""
        self.voidcmd("TYPE I")
        conn = self.transfercmd(cmd, rest)
        try:
            while True:
                bloc = fp.read(blocksize)
                if not bloc:
                    break
                conn.sendall(bloc)
        finally:
            conn.close()
        return self.voidresp()  # le serveur confirme la réception (code 226)


def se_connecter(reglages):
    """Ouvre la connexion FTP (chiffrée si possible) et renvoie l'objet connexion."""
    titre("3. Connexion au serveur FTP")
    mot_de_passe = reglages["mot_de_passe"]
    if not mot_de_passe:
        # getpass : la saisie ne s'affiche pas à l'écran, c'est normal.
        mot_de_passe = getpass.getpass("Mot de passe FTP de " + reglages["utilisateur"] + " : ")

    try:
        if reglages["chiffrement"]:
            print("Connexion chiffrée (FTPS) à " + reglages["hote"] + "...")
            contexte = ssl.create_default_context()
            # TLS 1.2 maximum : avec TLS 1.3, la réutilisation de session des
            # connexions de données échoue sur de nombreux serveurs FTP.
            contexte.maximum_version = ssl.TLSVersion.TLSv1_2
            ftp = FTPSAvecReprise(context=contexte, timeout=reglages["delai"])
            ftp.connect(reglages["hote"], reglages["port"])
            ftp.auth()               # passe la connexion de commande en TLS
            ftp.login(reglages["utilisateur"], mot_de_passe)
            ftp.prot_p()             # chiffre aussi les fichiers transférés
        else:
            print("ATTENTION : connexion NON chiffrée (FTP simple) à " + reglages["hote"] + "...")
            ftp = ftplib.FTP(timeout=reglages["delai"])
            ftp.connect(reglages["hote"], reglages["port"])
            ftp.login(reglages["utilisateur"], mot_de_passe)
    except ftplib.error_perm as erreur:
        arreter("connexion refusée par le serveur : " + str(erreur) + "\nVérifie l'identifiant et le mot de passe.")
    except (OSError, ftplib.Error) as erreur:
        arreter("impossible de joindre " + reglages["hote"] + " : " + str(erreur))

    ftp.set_pasv(True)  # mode passif : fonctionne derrière une box internet
    if reglages.get("detail"):
        ftp.set_debuglevel(1)  # affiche tout le dialogue FTP (*cmd* / *resp*)
    print("Connecté. Message du serveur : " + ftp.getwelcome().splitlines()[0])
    return ftp


# ===========================================================================
# ÉTAPE 4 : envoi des fichiers
# ===========================================================================
def existe_sur_serveur(ftp, chemin_distant):
    """Vrai si le fichier existe déjà sur le serveur (on demande sa taille)."""
    try:
        ftp.voidcmd("TYPE I")  # mode binaire, nécessaire pour la commande SIZE
        ftp.size(chemin_distant)
        return True
    except ftplib.error_perm:
        return False


def creer_dossiers(ftp, dossier_distant, deja_crees):
    """Crée un dossier distant et ses parents s'ils manquent (comme mkdir -p)."""
    parties = [p for p in dossier_distant.split("/") if p]
    chemin = ""
    for partie in parties:
        chemin += "/" + partie
        if chemin in deja_crees:
            continue
        try:
            ftp.mkd(chemin)
            print("  dossier créé : " + chemin)
        except ftplib.error_perm:
            pass  # le dossier existe déjà : ce n'est pas une erreur
        deja_crees.add(chemin)


def envoyer(ftp, reglages, commit, a_envoyer, a_supprimer, essai):
    """Envoie et supprime les fichiers. En mode essai, affiche seulement."""
    titre("4. Envoi des fichiers" + (" (ESSAI : rien n'est envoyé)" if essai else ""))
    dossier = reglages["dossier_local"]
    racine = reglages["dossier_distant"]
    deja_crees = set()
    envoyes, proteges, supprimes = 0, 0, 0

    for chemin in a_envoyer:
        distant = racine + "/" + chemin

        # Fichiers protégés (contenu modifié en ligne depuis /admin) :
        # on les envoie seulement s'ils n'existent pas encore sur le serveur.
        if est_exclu(chemin, reglages["proteger"]) and ftp is not None:
            if existe_sur_serveur(ftp, distant):
                print("  protégé, laissé tel quel : " + chemin)
                proteges += 1
                continue

        if essai:
            print("  enverrait : " + chemin)
            envoyes += 1
            continue

        # Contenu du fichier tel qu'il est dans le commit (git show commit:chemin).
        # Ainsi on envoie exactement la version enregistrée dans Git.
        contenu = subprocess.run(
            ["git", "show", commit + ":" + chemin],
            cwd=str(dossier), capture_output=True, check=True,
        ).stdout

        creer_dossiers(ftp, distant.rsplit("/", 1)[0], deja_crees)
        # On affiche le nom AVANT l'envoi (flush = tout de suite) : en cas de
        # blocage, on voit sur quel fichier ça coince.
        print("  envoi : " + chemin + " (" + str(len(contenu)) + " octets)...", end=" ", flush=True)
        # STOR : commande FTP qui dépose un fichier sur le serveur.
        ftp.storbinary("STOR " + distant, io.BytesIO(contenu))
        print("ok")
        envoyes += 1

    if reglages["supprimer"]:
        for chemin in a_supprimer:
            if est_exclu(chemin, reglages["proteger"]):
                print("  protégé, pas supprimé : " + chemin)
                continue
            if essai:
                print("  supprimerait : " + chemin)
                supprimes += 1
                continue
            try:
                ftp.delete(racine + "/" + chemin)  # DELE : supprime un fichier distant
                print("  supprimé : " + chemin)
                supprimes += 1
            except ftplib.error_perm:
                print("  déjà absent du serveur : " + chemin)

    print()
    if essai:
        print("Bilan de l'essai : " + str(envoyes) + " à envoyer, " + str(supprimes) + " à supprimer, "
              + str(proteges) + " protégé(s). Rien n'a été modifié sur le serveur.")
    else:
        print("Bilan : " + str(envoyes) + " envoyé(s), " + str(supprimes) + " supprimé(s), "
              + str(proteges) + " protégé(s).")


def installer_htaccess_racine(ftp, reglages, essai):
    """
    Place le fichier .htaccess de la racine du site (voir infomaniak/htaccess-racine.txt).
    Il fait afficher portfolio/public/ à l'adresse du site et bloque l'accès
    direct au code et aux données. Ce fichier ne fait pas partie du portfolio :
    il dépend de l'hébergeur, c'est pourquoi il est rangé avec le script.
    """
    if not reglages["htaccess_local"]:
        return  # rien à faire : option désactivée dans config.ini
    titre("5. Fichier .htaccess de la racine du site")
    local = Path(reglages["htaccess_local"])
    if not local.is_absolute():
        local = DOSSIER_SCRIPT / local
    voulu = local.read_bytes()
    distant = reglages["htaccess_distant"]

    # On télécharge la version du serveur (RETR) pour la comparer.
    actuel = io.BytesIO()
    try:
        ftp.retrbinary("RETR " + distant, actuel.write)
        actuel = actuel.getvalue()
    except ftplib.error_perm:
        actuel = None  # pas encore de .htaccess sur le serveur

    if actuel == voulu:
        print("  " + distant + " est déjà à jour.")
        return
    if essai:
        print("  " + ("remplacerait : " if actuel is not None else "enverrait : ") + distant)
        return
    if actuel is not None:
        # Un autre .htaccess existait : on en garde une copie avant de le remplacer.
        copie = DOSSIER_SCRIPT / ("sauvegarde-htaccess-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".txt")
        copie.write_bytes(actuel)
        print("  ancien " + distant + " sauvegardé dans " + copie.name)
    ftp.storbinary("STOR " + distant, io.BytesIO(voulu))
    print("  envoyé : " + distant)


def memoriser_deploiement(reglages, commit):
    """Note le commit déployé, pour ne renvoyer que les changements la prochaine fois."""
    etat = lire_etat()
    etat[cle_serveur(reglages)] = {
        "commit": commit,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    FICHIER_ETAT.write_text(json.dumps(etat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Commit " + commit[:7] + " noté dans " + FICHIER_ETAT.name + ".")


# ===========================================================================
# Programme principal : enchaîne les 4 étapes
# ===========================================================================
def main():
    options = argparse.ArgumentParser(description="Déploie le portfolio de Git vers FTP.")
    options.add_argument("--essai", action="store_true", help="montre ce qui serait fait, sans rien envoyer")
    options.add_argument("--tout", action="store_true", help="renvoie tous les fichiers")
    options.add_argument("--detail", action="store_true", help="affiche le dialogue FTP complet (dépannage)")
    options.add_argument("--config", default=str(DOSSIER_SCRIPT / "config.ini"), help="fichier de configuration")
    args = options.parse_args()

    reglages = lire_configuration(Path(args.config))
    reglages["detail"] = args.detail
    commit = recuperer_depot(reglages)
    a_envoyer, a_supprimer = lister_changements(reglages, commit, args.tout)

    if not a_envoyer and not a_supprimer and not reglages["htaccess_local"]:
        print("\nLe serveur est déjà à jour. Fin.")
        return

    ftp = se_connecter(reglages)
    try:
        if a_envoyer or a_supprimer:
            envoyer(ftp, reglages, commit, a_envoyer, a_supprimer, args.essai)
        else:
            print("\nAucun fichier du portfolio à envoyer : le serveur est déjà à jour.")
        installer_htaccess_racine(ftp, reglages, args.essai)
    except (OSError, ftplib.Error) as erreur:
        # On ne note pas le commit : le prochain lancement renverra les mêmes fichiers.
        arreter("l'envoi a été interrompu : " + str(erreur) + "\nRelance le script, il reprendra.")
    finally:
        try:
            ftp.quit()  # QUIT : on se déconnecte proprement
        except (OSError, ftplib.Error):
            ftp.close()

    if not args.essai:
        memoriser_deploiement(reglages, commit)
        print("\nDéploiement terminé.")
    else:
        print("\nEssai terminé. Pour envoyer pour de vrai : python3 deployer.py")


# Cette ligne lance main() quand on exécute le fichier avec « python3 deployer.py ».
if __name__ == "__main__":
    main()
