# SPDX-License-Identifier: GPL-3.0-or-later
"""French / English interface.

* Labels, tooltips and menus of the add-on (properties, panels, operators) go through Blender's translation system
  with our own context "FPV": only our dictionary translates them, so Blender's own words are never changed.
* Language = Auto (French when Blender is in French), Français (always, even in an English Blender) or English.
* Texts built at run time (reports, OSD, status bar) use T().
"""
import bpy

CTX = "FPV"
_ON = [False]
_REGISTERED = [False]


def _prefs():
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except Exception:
        return None


def language_pref():
    p = _prefs()
    return getattr(p, "language", 'AUTO') if p is not None else 'AUTO'


def is_fr():
    lp = language_pref()
    if lp == 'FR':
        return True
    if lp == 'EN':
        return False
    try:
        return bpy.app.translations.locale.startswith("fr")
    except Exception:
        return False


def refresh():
    _ON[0] = is_fr()
    return _ON[0]


def T(msg):
    """Run-time text in the current add-on language."""
    if _ON[0] and msg:
        return FR.get(msg, msg)
    return msg


def _messages():
    msgs = {}
    for en, fr in FR.items():
        msgs[(CTX, en)] = fr
        if en.count(" ") >= 3:            # tooltips: also under the default context (long, unique sentences)
            msgs[("*", en)] = fr
    return msgs


def _register_translations():
    tr = bpy.app.translations
    if _REGISTERED[0]:
        try:
            tr.unregister(__package__)
        except Exception:
            pass
        _REGISTERED[0] = False
    lp = language_pref()
    if lp == 'EN':
        return
    msgs = _messages()
    if lp == 'FR':                          # whatever Blender's language
        locales = set(tr.locales) | {"en_US", "fr_FR", "fr"}
    else:
        locales = {"fr_FR", "fr"}
    tr.register(__package__, {loc: msgs for loc in locales})
    _REGISTERED[0] = True


def _redraw_all():
    try:
        for w in bpy.context.window_manager.windows:
            for a in w.screen.areas:
                a.tag_redraw()
    except Exception:
        pass


def apply_language():
    _register_translations()
    refresh()
    _redraw_all()


def set_language(lang):
    p = _prefs()
    if p is None:
        return
    p.language = lang                       # the update callback applies it
    try:
        bpy.context.preferences.is_dirty = True
    except Exception:
        pass


def register():
    _register_translations()
    refresh()


def unregister():
    if _REGISTERED[0]:
        try:
            bpy.app.translations.unregister(__package__)
        except Exception:
            pass
        _REGISTERED[0] = False


# ======================================================================================================== French
FR = {
    # ------------------------------------------------------------------------------------------ panels
    "FPV Drone": "FPV Drone",
    "Controller": "Manette",
    "Flight": "Vol",
    "Motion (Phone Tilt)": "Motion (inclinaison du téléphone)",
    "Realism": "Réalisme",
    "Physical Specs": "Caractéristiques physiques",
    "Physics Cam (Polyfjord style)": "Physics Cam (façon Polyfjord)",
    "FPV Look (render)": "Look FPV (rendu)",
    "Scale, Collisions & Crash": "Échelle, collisions et crash",
    "Camera": "Caméra",
    "Camera Culling": "Camera Culling",
    "Performance": "Performance",
    "Recording": "Enregistrement",
    "Phone": "Téléphone",
    "Language & Updates": "Langue et mises à jour",

    # ------------------------------------------------------------------------------------------ main panel
    "Flying...  ESC to stop": "En vol...  Échap pour arrêter",
    "Speed ×%g   (wheel / + -)": "Vitesse ×%g   (molette / + -)",
    "Fly + Record": "Voler + Enregistrer",
    "Fly": "Voler",
    "Type": "Type",
    "Speed ×": "Vitesse ×",
    "Back to Start Point": "Retour au point de départ",
    "Update %s available": "Mise à jour %s disponible",
    "Install Update": "Installer la mise à jour",
    "Downloading the update...": "Téléchargement de la mise à jour...",
    "Installing the update...": "Installation de la mise à jour...",
    "Checking for updates...": "Recherche de mises à jour...",
    "Update installed": "Mise à jour installée",
    "FPV Drone %s is up to date": "FPV Drone %s est à jour",
    "Update: %s": "Mise à jour : %s",

    # ------------------------------------------------------------------------------------------ controller panel
    "Preset:": "Préréglage :",
    "Radio": "Radio",
    "Xbox": "Xbox",
    "Keyboard": "Clavier",
    "Phone sticks: nothing to map, see the Phone panel": "Sticks du téléphone : rien à régler, voir le panneau Téléphone",
    "No controller found: keyboard": "Aucune manette trouvée : clavier",
    "Z/S throttle   Q/D yaw": "Z/S gaz   Q/D lacet",
    "W/S throttle   A/D yaw": "W/S gaz   A/D lacet",
    "Z or W / S throttle   Q or A / D yaw": "Z ou W / S gaz   Q ou A / D lacet",
    "Arrows pitch / roll": "Flèches : tangage / roulis",
    "Shift full stick   Ctrl fine": "Maj stick à fond   Ctrl précis",
    "Wheel or + / - : speed": "Molette ou + / - : vitesse",
    "Stop Test": "Arrêter le test",
    "Test Controller": "Tester la manette",
    "Axis": "Axe",
    "Throttle": "Gaz",
    "Yaw": "Lacet",
    "Pitch": "Tangage",
    "Roll": "Roulis",
    "Left X": "Gauche X",
    "Left Y": "Gauche Y",
    "Right X": "Droite X",
    "Right Y": "Droite Y",
    "Left Trigger": "Gâchette gauche",
    "Right Trigger": "Gâchette droite",

    # ------------------------------------------------------------------------------------------ flight / specs panels
    "Roll / Pitch": "Roulis / tangage",
    "Max rate: %d deg/s  (yaw %d)": "Vitesse max : %d °/s  (lacet %d)",
    "Thrust/Weight  %.1f   (hover at %d%% throttle)": "Poussée/poids  %.1f   (stationnaire à %d %% de gaz)",
    "Top speed  ≈ %d km/h   (%d° nose down)": "Vitesse de pointe  ≈ %d km/h   (nez à %d° vers le bas)",
    "With Speed ×%g: ≈ %d km/h in the scene": "Avec vitesse ×%g : ≈ %d km/h dans la scène",
    "Rotation accel  %d °/s²": "Accélération en rotation  %d °/s²",
    "Too heavy: it cannot take off": "Trop lourd : il ne peut pas décoller",
    "Select Mode: Motion (or 'Tilt' on the phone)": "Choisis le mode Motion (ou « Tilt » sur le téléphone)",
    "1. A target to chase:": "1. Une cible à poursuivre :",
    "Uses Physical Specs, collisions, vibration, horizon lock": "Utilise les caractéristiques physiques, les collisions, la vibration et le verrouillage d'horizon",
    "Update FPV Look": "Mettre à jour le look FPV",

    # ------------------------------------------------------------------------------------------ culling / perf panels
    "Hidden objects come back when you stop flying": "Les objets masqués réapparaissent à l'arrêt du vol",
    "Everything is restored when you stop flying": "Tout est restauré à l'arrêt du vol",
    "Blender 5.0.0 has known bugs: update to 5.3 (or 5.2 LTS)": "Blender 5.0.0 a des bugs connus : passe en 5.3 (ou 5.2 LTS)",
    "Scene optimized (tiles)": "Scène optimisée (tuiles)",

    # ------------------------------------------------------------------------------------------ phone panel
    "Stop Phone Link": "Arrêter le lien téléphone",
    "Start Phone Link": "Démarrer le lien téléphone",
    "Phone and PC on the same Wi-Fi": "Téléphone et PC sur le même Wi-Fi",
    "Phone connected": "Téléphone connecté",
    "Waiting for the phone...": "En attente du téléphone...",
    "Secure link (needed for Tilt).": "Lien sécurisé (nécessaire pour Tilt).",
    "First visit: 'Advanced' > 'Proceed'.": "Première visite : « Paramètres avancés » > « Continuer ».",
    "No HTTPS: sticks only (%s)": "Pas de HTTPS : sticks seulement (%s)",
    "Input is not Phone: the FLY button on the phone will switch it": "L'entrée n'est pas Téléphone : le bouton FLY du téléphone la changera",
    "Scan with your phone (same Wi-Fi)": "Scanne avec ton téléphone (même Wi-Fi)",

    # ------------------------------------------------------------------------------------------ OSD
    "TIME x%.2f": "TEMPS x%.2f",
    "SPEED x%g": "VITESSE x%g",
    "throttle down to arm": "gaz en bas pour armer",
    "DISARMED - release the accelerator": "DÉSARMÉ - relâche l'accélérateur",
    "DISARMED - lower the throttle to arm": "DÉSARMÉ - baisse les gaz pour armer",
    "ARMED": "ARMÉ",
    "ON GROUND": "AU SOL",
    "LOOK %+.0f°": "VISÉE %+.0f°",
    "PROPWASH": "PROPWASH",
    "CRASHED": "CRASH",
    "R = back to start    ESC = stop": "R = retour au départ    Échap = arrêter",
    "IMPACT  %.0f m/s": "IMPACT  %.0f m/s",
    "ESC stop   R reset   wheel speed": "Échap stop   R départ   molette vitesse",
    "CULL %d/%d hidden  %.0f ms": "CULLING %d/%d masqués  %.0f ms",
    "start point is below Floor Height: floor disabled": "le point de départ est sous la hauteur du sol : sol désactivé",

    # ------------------------------------------------------------------------------------------ status bar / reports
    "FPV  |  ESC / Right-click: stop   R: reset to start   Wheel or + -: speed":
        "FPV  |  Échap / clic droit : arrêter   R : retour au départ   Molette ou + - : vitesse",
    "   |   W/S throttle  A/D yaw  arrows pitch/roll  Shift full  Ctrl fine":
        "   |   Z/W S gaz  Q/A D lacet  flèches tangage/roulis  Maj à fond  Ctrl précis",
    "FPV: other add-ons paused while flying: %s": "FPV : autres addons mis en pause pendant le vol : %s",
    "FPV: the take could not be written: %s": "FPV : la prise n'a pas pu être écrite : %s",
    "FPV live: %.0f img/s (target %.0f), image interval %.1f ms median / %.1f ms worst, %d hitches":
        "FPV en direct : %.0f img/s (cible %.0f), intervalle %.1f ms médian / %.1f ms au pire, %d à-coups",
    "FPV take recorded: %d frames (%d -> %d), top speed %.0f km/h":
        "Prise FPV enregistrée : %d images (%d -> %d), vitesse max %.0f km/h",
    "FPV rig created: %s / %s": "Rig FPV créé : %s / %s",
    "%d keys removed": "%d clés supprimées",
    "Keyboard input: nothing to test, keys are W/S, A/D and the arrows":
        "Entrée clavier : rien à tester, les touches sont Z/W S, Q/A D et les flèches",
    "Keyboard mapping is fixed": "Les touches du clavier sont fixes",
    "FPV Learn %s: keep sticks still, then %s  (ESC cancel)": "FPV Apprendre %s : ne touche pas les sticks, puis %s  (Échap annule)",
    "push THROTTLE fully UP": "pousse les GAZ à fond vers le HAUT",
    "push YAW fully RIGHT": "pousse le LACET à fond à DROITE",
    "push PITCH fully FORWARD (up)": "pousse le TANGAGE à fond vers l'AVANT (haut)",
    "push ROLL fully RIGHT": "pousse le ROULIS à fond à DROITE",
    "%s = axis %d%s": "%s = axe %d%s",
    " (inverted)": " (inversé)",
    "Controller disconnected": "Manette déconnectée",
    "No stick movement detected": "Aucun mouvement de stick détecté",
    "Phone link stopped": "Lien téléphone arrêté",
    "Phone link: ": "Lien téléphone : ",
    "Cannot open port %d (%s). Try another port.": "Impossible d'ouvrir le port %d (%s). Essaie un autre port.",
    "HTTPS unavailable (%s): sticks only, no tilt mode": "HTTPS indisponible (%s) : sticks seulement, pas de mode Tilt",
    "Link copied": "Lien copié",
    "Nothing to optimize (no visible mesh above %d faces, or already done)":
        "Rien à optimiser (aucun mesh visible au-dessus de %d faces, ou déjà fait)",
    "%s: %dk faces -> %d tiles%s": "%s : %dk faces -> %d tuiles%s",
    " x%d objects": " x%d objets",
    "Optimized in %.1fs: %s": "Optimisé en %.1f s : %s",
    "Removed %d tiles, restored %d objects": "%d tuiles supprimées, %d objets restaurés",
    "Physics Cam baked: %d frames in %.1fs, top %.0f km/h, max lag %.1f m, %d impacts":
        "Physics Cam calculée : %d images en %.1f s, max %.0f km/h, retard max %.1f m, %d impacts",
    "Target %s created: average %.0f km/h": "Cible %s créée : moyenne %.0f km/h",
    "Target %s created from the take": "Cible %s créée à partir de la prise",
    "Target created: move it and press I to key it along the shot": "Cible créée : déplace-la et appuie sur I pour l'animer le long du plan",
    "FPV Look on (compositor group '%s'). Visible in the render / camera view with the compositor":
        "Look FPV activé (groupe de compositing « %s »). Visible au rendu / en vue caméra avec le compositeur",
    "Blender is already optimized": "Blender est déjà optimisé",
    "Optimized (%d settings)%s: %s": "Optimisé (%d réglages)%s : %s",
    " - RESTART Blender for the OpenGL backend": " - REDÉMARRE Blender pour passer en OpenGL",
    "%d settings restored": "%d réglages restaurés",
    "Stop flying before updating": "Arrête le vol avant de mettre à jour",

    # ------------------------------------------------------------------------------------------ errors
    "No drone: create an FPV rig or pick an object in the FPV Drone panel":
        "Pas de drone : crée un rig FPV ou choisis un objet dans le panneau FPV Drone",
    "The drone object must not be parented (fly the parent instead)": "L'objet drone ne doit pas avoir de parent (fais voler le parent)",
    "Motion mode is driven by the phone: start the Phone Link and use Input = Phone (or Auto)":
        "Le mode Motion se pilote au téléphone : démarre le lien téléphone et mets Entrée = Téléphone (ou Auto)",
    "Phone link is not running: start it in the FPV Drone > Phone panel": "Le lien téléphone n'est pas démarré : lance-le dans FPV Drone > Téléphone",
    "winmm.dll is not available (Windows only)": "winmm.dll n'est pas disponible (Windows uniquement)",
    "XInput is not available (Windows only)": "XInput n'est pas disponible (Windows uniquement)",
    "pygame is not installed in Blender's Python": "pygame n'est pas installé dans le Python de Blender",
    "No drone: create an FPV rig first": "Pas de drone : crée d'abord un rig FPV",
    "Pick a Target (or create one from a curve / from your recorded take)": "Choisis une cible (ou crée-la depuis une courbe / ta prise enregistrée)",
    "The target must be a separate object (not the drone)": "La cible doit être un autre objet (pas le drone)",
    "The drone object must not be parented": "L'objet drone ne doit pas avoir de parent",
    "Frame range is empty": "La plage d'images est vide",
    "No drone": "Pas de drone",
    "The drone has no recorded take (record a flight first)": "Le drone n'a pas de prise enregistrée (enregistre d'abord un vol)",
    "Select a curve (the path to fly)": "Sélectionne une courbe (la trajectoire à suivre)",
    "The curve is too short": "La courbe est trop courte",
    "Online access is disabled in Blender's preferences (System > Network)":
        "L'accès en ligne est désactivé dans les préférences de Blender (Système > Réseau)",
    "no release published yet": "aucune release publiée pour l'instant",
    "No update available": "Aucune mise à jour disponible",
    "Legacy add-on install: download the new version from the page that just opened":
        "Installation classique : télécharge la nouvelle version sur la page qui vient de s'ouvrir",
    "wrong file size": "taille de fichier incorrecte",
    "wrong checksum": "empreinte de fichier incorrecte",

    # ------------------------------------------------------------------------------------------ Optimize Blender (tune)
    "viewport anti-aliasing: FXAA instead of 8 samples": "anticrénelage de la vue : FXAA au lieu de 8 échantillons",
    "no smooth overlay wires": "fils d'overlay non lissés",
    "no smooth edit-mode wires": "fils du mode édition non lissés",
    "textures capped at 2048 px in the viewport (less VRAM, no stalls)": "textures limitées à 2048 px dans la vue (moins de VRAM, pas de gel)",
    "light texture filtering": "filtrage de texture léger",
    "subdivision on the GPU": "subdivision sur le GPU",
    "EEVEE shaders compiled in parallel processes: no freeze": "shaders EEVEE compilés dans des processus séparés : pas de gel",
    "instant view changes": "changements de vue instantanés",
    "fewer undo steps kept in memory": "moins d'annulations gardées en mémoire",
    "no scene statistics in the status bar (recounted on every change)": "pas de statistiques de scène dans la barre d'état (recomptées à chaque changement)",
    "EEVEE viewport: 4 samples instead of 16": "EEVEE dans la vue : 4 échantillons au lieu de 16",
    "no soft-shadow jitter in the viewport": "pas de jitter des ombres douces dans la vue",
    "temporal reprojection (cleaner image while moving)": "reprojection temporelle (image plus propre en mouvement)",
    "OpenGL: the most stable / fastest backend before 5.3 (restart needed)": "OpenGL : le backend le plus stable / rapide avant la 5.3 (redémarrage nécessaire)",

    # ------------------------------------------------------------------------------------------ operators
    "Fly FPV": "Voler en FPV",
    "Fly the drone. ESC / right click: stop.  R: back to the start point.  Mouse wheel: speed":
        "Faire voler le drone. Échap / clic droit : arrêter.  R : retour au départ.  Molette : vitesse",
    "Create FPV Rig": "Créer un rig FPV",
    "Create an FPV quad rig (body empty + uptilted camera + proxy) at the 3D cursor":
        "Créer un rig de drone FPV (empty du corps + caméra inclinée + proxy) au curseur 3D",
    "Put the drone back where the last flight started": "Remettre le drone là où le dernier vol a commencé",
    "Clear Take": "Effacer la prise",
    "Delete the drone keyframes from the recording start frame onward": "Supprimer les clés du drone à partir de l'image de départ de l'enregistrement",
    "Current Frame": "Image actuelle",
    "Use the current frame as recording start": "Utiliser l'image actuelle comme départ de l'enregistrement",
    "Controller Preset": "Préréglage de manette",
    "Apply a controller mapping preset (Mode 2)": "Appliquer un préréglage d'axes (Mode 2)",
    "RC Radio": "Radiocommande",
    "EdgeTX / OpenTX radio in USB joystick mode": "Radio EdgeTX / OpenTX en mode joystick USB",
    "Xbox Gamepad": "Manette Xbox",
    "Show the live controller axes in the panel (click again or ESC to stop)":
        "Afficher les axes de la manette en direct dans le panneau (reclique ou Échap pour arrêter)",
    "Learn Axis": "Apprendre l'axe",
    "Detect which controller axis drives this channel: click, then move only that stick":
        "Détecter quel axe de la manette pilote cette voie : clique, puis bouge uniquement ce stick",
    "Phone Link": "Lien téléphone",
    "Start / stop the phone link: open the link (or scan the QR code) on a phone connected to the same Wi-Fi":
        "Démarrer / arrêter le lien téléphone : ouvre le lien (ou scanne le QR code) sur un téléphone connecté au même Wi-Fi",
    "Copy Link": "Copier le lien",
    "Copy the phone link to the clipboard (send it to your phone)": "Copier le lien téléphone dans le presse-papiers (envoie-le sur ton téléphone)",
    "Optimize Scene for Flight": "Optimiser la scène pour le vol",
    "Split very heavy meshes into tiles so Blender only draws what the drone camera sees (reversible)":
        "Découper les meshes très lourds en tuiles pour que Blender n'affiche que ce que voit la caméra du drone (réversible)",
    "Restore Original Meshes": "Restaurer les meshes d'origine",
    "Remove the flight tiles and bring the original meshes back": "Supprimer les tuiles de vol et remettre les meshes d'origine",
    "Bake Physics Cam": "Calculer la Physics Cam",
    "Simulate the drone chasing the target (Polyfjord-style physics FPV) and bake one key per frame":
        "Simuler le drone qui poursuit la cible (FPV physique façon Polyfjord) et poser une clé par image",
    "Target from Curve": "Cible depuis une courbe",
    "Create an animated target that travels along the selected curve over the scene frame range":
        "Créer une cible animée qui parcourt la courbe sélectionnée sur la plage d'images de la scène",
    "Target from My Take": "Cible depuis ma prise",
    "Turn the flight you recorded into a target: the bake re-flies it with clean physics":
        "Transformer ton vol enregistré en cible : le calcul le revole avec une physique propre",
    "New Target": "Nouvelle cible",
    "Create an empty target at the drone, with a first key: move it and key it along the shot":
        "Créer une cible (empty) au drone avec une première clé : déplace-la et anime-la le long du plan",
    "Apply FPV Look": "Appliquer le look FPV",
    "FPV action-cam finish: motion blur, wide-angle distortion, chromatic aberration, bloom, vignette (compositor)":
        "Finition caméra d'action FPV : flou de mouvement, distorsion grand-angle, aberration chromatique, bloom, vignette (compositing)",
    "Remove FPV Look": "Retirer le look FPV",
    "Remove the FPV Look and restore the previous compositor and motion blur settings":
        "Retirer le look FPV et restaurer le compositing et le flou de mouvement d'avant",
    "Optimize Blender": "Optimiser Blender",
    "Set Blender's preferences and this file's viewport for maximum performance (reversible)":
        "Régler les préférences de Blender et la vue de ce fichier pour des performances maximales (réversible)",
    "Restore Blender Settings": "Restaurer les réglages de Blender",
    "Put back the preferences and viewport settings changed by 'Optimize Blender'":
        "Remettre les préférences et réglages de vue modifiés par « Optimiser Blender »",
    "Language": "Langue",
    "Switch the add-on language: Français / English": "Changer la langue de l'addon : Français / English",
    "Check for Updates": "Rechercher des mises à jour",
    "Look for a new version of FPV Drone on GitHub (releases)": "Chercher une nouvelle version de FPV Drone sur GitHub (releases)",
    "Download the new version from GitHub and install it (Blender reloads the add-on)":
        "Télécharger la nouvelle version depuis GitHub et l'installer (Blender recharge l'addon)",
    "Release Notes": "Notes de version",
    "Open the release page on GitHub (changes of the new version)": "Ouvrir la page de la release sur GitHub (nouveautés de la version)",
    "Français": "Français",
    "English": "English",
    "Auto": "Auto",

    # ------------------------------------------------------------------------------------------ preferences
    "Auto (Blender)": "Auto (Blender)",
    "French if Blender's interface is in French, else English": "Français si l'interface de Blender est en français, sinon anglais",
    "Always in French, whatever Blender's language": "Toujours en français, quelle que soit la langue de Blender",
    "Always in English": "Toujours en anglais",
    "Look for a new release on GitHub when Blender starts (at most once a day)":
        "Chercher une nouvelle release sur GitHub au démarrage de Blender (au plus une fois par jour)",
    "Install Automatically": "Installer automatiquement",
    "Install a new release by itself (never during a flight). Off = a button appears in the panel":
        "Installer seule une nouvelle release (jamais pendant un vol). Désactivé = un bouton apparaît dans le panneau",
    "GitHub Repository": "Dépôt GitHub",
    "owner/name of the GitHub repository whose releases are installed": "propriétaire/nom du dépôt GitHub dont les releases sont installées",

    # ------------------------------------------------------------------------------------------ properties: input
    "Drone": "Drone",
    "Object to fly: an FPV rig (recommended), a camera, or any object": "Objet à faire voler : un rig FPV (conseillé), une caméra ou n'importe quel objet",
    "Input": "Entrée",
    "First controller found (XInput, then joystick, then pygame), else keyboard":
        "Première manette trouvée (XInput, puis joystick, puis pygame), sinon le clavier",
    "Radio / Joystick": "Radio / joystick",
    "Any USB joystick on Windows: RC transmitter in joystick mode, flight stick, gamepad":
        "Tout joystick USB sous Windows : radiocommande en mode joystick, manche, manette",
    "Xbox / XInput controller (Windows)": "Manette Xbox / XInput (Windows)",
    "Your phone as a touch radio, through the Phone Link (same Wi-Fi)": "Ton téléphone comme radio tactile, via le lien téléphone (même Wi-Fi)",
    "Pygame": "Pygame",
    "Only if pygame is already installed in Blender's Python (any OS)": "Seulement si pygame est déjà installé dans le Python de Blender (tout OS)",
    "W/S throttle, A/D yaw, arrows pitch/roll, Shift = full stick, Ctrl = fine":
        "Z/W S gaz, Q/A D lacet, flèches tangage/roulis, Maj = stick à fond, Ctrl = précis",
    "Device": "Périphérique",
    "Invert": "Inverser",
    "Throttle Range": "Course des gaz",
    "Full Travel": "Course complète",
    "Stick bottom = 0 %, top = 100 % (RC radio, non-centering stick)": "Stick en bas = 0 %, en haut = 100 % (radiocommande, stick sans ressort)",
    "Upper Half": "Moitié haute",
    "Centre = 0 %, top = 100 % (spring-centred gamepad stick)": "Centre = 0 %, haut = 100 % (stick de manette à ressort)",
    "Deadband": "Zone morte",
    # flight
    "Mode": "Mode",
    "Acro": "Acro",
    "Rate mode, like a real FPV quad: sticks command rotation speed": "Mode rate, comme un vrai drone FPV : les sticks commandent la vitesse de rotation",
    "Angle": "Angle",
    "Self-levelling: sticks command tilt angle. Easier with keyboard or gamepad":
        "Auto-stabilisé : les sticks commandent l'inclinaison. Plus simple au clavier ou à la manette",
    "Motion": "Motion",
    "Phone tilt: point the phone to look and steer, accelerator to fly forward (DJI Motion style)":
        "Inclinaison du téléphone : pointe le téléphone pour viser et diriger, accélérateur pour avancer (façon DJI Motion)",
    "AZERTY + QWERTY": "AZERTY + QWERTY",
    "Z/W and S throttle, Q/A and D yaw": "Z/W et S gaz, Q/A et D lacet",
    "AZERTY (ZQSD)": "AZERTY (ZQSD)",
    "QWERTY (WASD)": "QWERTY (WASD)",
    "Max Speed (km/h)": "Vitesse max (km/h)",
    "Max Accel (m/s²)": "Accélération max (m/s²)",
    "How hard the drone speeds up / brakes. Lower = smoother": "Force d'accélération / de freinage du drone. Plus bas = plus doux",
    "Max Tilt": "Inclinaison max",
    "Steering": "Direction",
    "Tilt to Turn": "Incliner pour tourner",
    "Tilt the phone left/right like a steering wheel (DJI Motion)": "Incline le téléphone à gauche / à droite comme un volant (DJI Motion)",
    "Point to Turn": "Pointer pour tourner",
    "The drone faces where the phone faces (turn your body)": "Le drone regarde là où regarde le téléphone (tourne-toi)",
    "Slider Only": "Curseur seulement",
    "Phone tilt only aims the camera up/down; turn with the rotation slider": "L'inclinaison ne sert qu'à viser en haut / en bas ; on tourne avec le curseur de rotation",
    "Rotation Speed": "Vitesse de rotation",
    "Yaw speed with the rotation slider at full travel": "Vitesse de lacet avec le curseur de rotation à fond",
    "Turn Rate": "Vitesse de virage",
    "Yaw speed at full phone tilt": "Vitesse de lacet téléphone incliné au maximum",
    "Look Gain": "Gain de visée",
    "Max Look": "Visée max",
    "Fly Where You Look": "Voler là où tu regardes",
    "Climb / descend in the direction the phone points": "Monter / descendre dans la direction où pointe le téléphone",
    "Smoothing": "Lissage",
    "Filtering of the phone motion (higher = smoother, more cinematic)": "Filtrage des mouvements du téléphone (plus haut = plus doux, plus cinéma)",
    "RC Rate": "RC Rate",
    "Betaflight RC rate (roll/pitch)": "RC rate Betaflight (roulis / tangage)",
    "Super Rate": "Super Rate",
    "Expo": "Expo",
    "Yaw RC Rate": "RC Rate lacet",
    "Yaw Super Rate": "Super Rate lacet",
    "Yaw Expo": "Expo lacet",
    "Max Angle": "Angle max",
    "Angle mode tilt limit": "Inclinaison max en mode Angle",
    "Thrust/Weight": "Poussée/poids",
    "Full-throttle thrust divided by weight. Hover ≈ 1/TWR throttle": "Poussée plein gaz divisée par le poids. Stationnaire ≈ 1/TWR de gaz",
    "Throttle Expo": "Expo des gaz",
    "Drone Type": "Type de drone",
    "Cinewhoop 3\"": "Cinewhoop 3\"",
    "Ducted, slow and floaty (~50 km/h), low rates. Indoor / close-up shots":
        "Caréné, lent et flottant (~50 km/h), rates faibles. Intérieur / plans rapprochés",
    "Freestyle 5\"": "Freestyle 5\"",
    "Punchy freestyle quad, TWR 8, ~130-150 km/h": "Drone freestyle nerveux, TWR 8, ~130-150 km/h",
    "Cinematic 7\"": "Cinéma 7\"",
    "Smooth long-range cine quad (~115 km/h), carves wide lines": "Drone cinéma longue portée tout en douceur (~115 km/h), grandes courbes",
    "Cinelifter": "Cinelifter",
    "Heavy rig with a cinema camera (~70 km/h): slow rotations, lots of inertia":
        "Gros porteur avec caméra cinéma (~70 km/h) : rotations lentes, beaucoup d'inertie",
    "Racer 5\"": "Racer 5\"",
    "Race quad, TWR 13, very low drag (~180-200 km/h), 45° camera": "Drone de course, TWR 13, très peu de traînée (~180-200 km/h), caméra à 45°",
    "Custom": "Personnalisé",
    "Your own values": "Tes propres valeurs",
    "Use Real Specs": "Utiliser les vraies caractéristiques",
    "Set the drone from real-world values (mass, motor thrust, size, drag area). Off = edit the simulator coefficients directly":
        "Régler le drone avec des valeurs réelles (masse, poussée, taille, surface). Désactivé = régler directement les coefficients du simulateur",
    "Mass (g)": "Masse (g)",
    "All-up weight with battery and camera": "Poids total avec batterie et caméra",
    "Thrust per Motor (g)": "Poussée par moteur (g)",
    "Static thrust of one motor + prop at full throttle (from the motor's thrust table)":
        "Poussée statique d'un moteur + hélice plein gaz (tableau de poussée du moteur)",
    "Motors": "Moteurs",
    "Frame Size (mm)": "Taille du châssis (mm)",
    "Motor-to-motor diagonal (wheelbase)": "Diagonale moteur à moteur (empattement)",
    "Inertia": "Inertie",
    "How far the mass sits from the centre (0.3 compact, 0.6 typical, 1+ heavy camera/ducts on the outside). Higher = slower to rotate":
        "Répartition de la masse autour du centre (0,3 compact, 0,6 normal, 1+ grosse caméra / carénages). Plus haut = rotations plus lentes",
    "Front Area (cm²)": "Surface frontale (cm²)",
    "Area seen from the front: sets the top speed": "Surface vue de face : fixe la vitesse de pointe",
    "Top Area (cm²)": "Surface du dessus (cm²)",
    "Area seen from above: how much it 'catches the air' in dives and turns": "Surface vue de dessus : à quel point il « accroche l'air » en piqué et en virage",
    # physics cam
    "Target": "Cible",
    "Animated object the drone chases (empty, object on a path, your take)": "Objet animé que le drone poursuit (empty, objet sur un chemin, ta prise)",
    "Pull": "Traction",
    "Strength of the spring towards the target (Hz). Low = loose, floaty, big lag. High = tight, close to the target":
        "Force du ressort vers la cible (Hz). Bas = souple, flottant, beaucoup de retard. Haut = serré, près de la cible",
    "Damping": "Amortissement",
    "Below 1 the drone overshoots and swings in the corners (organic). 1 = no overshoot":
        "En dessous de 1, le drone dépasse et oscille dans les virages (organique). 1 = pas de dépassement",
    "Look Ahead": "Anticipation",
    "The nose aims where the target will be this much later": "Le nez vise là où sera la cible ce temps plus tard",
    "Yaw Smoothness": "Douceur du lacet",
    "Speed of the heading changes (Hz). Lower = slower, more cinematic pans": "Vitesse des changements de cap (Hz). Plus bas = panoramiques plus lents, plus cinéma",
    "Bank": "Inclinaison",
    "1 = real quad attitude (banks into turns, pitches to brake). 0 = level camera (gimbal-like)":
        "1 = attitude réelle du drone (penche dans les virages, se cabre pour freiner). 0 = caméra à plat (comme une nacelle)",
    "Pilot Limit (m/s²)": "Limite du pilote (m/s²)",
    "Cap what the pilot asks (0 = only the motors limit it)": "Limite ce que demande le pilote (0 = seuls les moteurs limitent)",
    "Start At": "Départ",
    "The drone starts on the target, already moving with it": "Le drone part sur la cible, déjà en mouvement avec elle",
    "The drone starts where it is now and catches up": "Le drone part de là où il est et rattrape la cible",
    "Ease In/Out": "Accélération / décélération",
    "Target from Curve: smooth start and stop along the path": "Cible depuis une courbe : départ et arrêt en douceur le long du chemin",
    "Seed": "Graine",
    "Turbulence variation": "Variation de la turbulence",
    # look
    "Strength": "Intensité",
    "Bloom and sharpening amount": "Quantité de bloom et de netteté",
    "Lens Distortion": "Distorsion de l'objectif",
    "Wide-angle barrel distortion of an action cam (0 = none)": "Distorsion en barillet grand-angle d'une caméra d'action (0 = aucune)",
    "Chromatic Aberration": "Aberration chromatique",
    "Vignette": "Vignettage",
    "Motion Blur (shutter)": "Flou de mouvement (obturateur)",
    "Shutter in frames: 0.5 = 180° like a real camera, 0 = off": "Obturateur en images : 0,5 = 180° comme une vraie caméra, 0 = désactivé",
    # realism
    "Realistic Physics": "Physique réaliste",
    "Motor lag, prop inflow, body drag, angular inertia, prop-wash, wind. Off = simple arcade model":
        "Latence moteurs, flux des hélices, traînée, inertie, prop-wash, vent. Désactivé = modèle arcade simple",
    "Drag Edge-on": "Traînée de profil",
    "Drag coefficient along the body X/Y axes (1/m). Sets the top speed": "Coefficient de traînée selon les axes X/Y du drone (1/m). Fixe la vitesse de pointe",
    "Drag Flat": "Traînée à plat",
    "Drag through the flat top plate (1/m). Higher = dives and turns 'catch the air'":
        "Traînée de la plaque du dessus (1/m). Plus haut = les piqués et virages « accrochent l'air »",
    "Linear Drag": "Traînée linéaire",
    "Angular Accel": "Accélération angulaire",
    "Max rotation acceleration per second (how heavy the quad feels)": "Accélération de rotation max par seconde (sensation de poids du drone)",
    "Motor Lag": "Latence des moteurs",
    "Motor spool-up time constant": "Temps de réponse des moteurs",
    "Prop Pitch Speed": "Vitesse de pas des hélices",
    "Air speed (m/s) at which the props stop producing thrust": "Vitesse de l'air (m/s) à laquelle les hélices ne poussent plus",
    "Prop-wash": "Prop-wash",
    "Shake when you descend through your own wash": "Secousses quand tu redescends dans ton propre souffle",
    "Turbulence": "Turbulence",
    "Wind (m/s)": "Vent (m/s)",
    "Ground Effect": "Effet de sol",
    "Battery Sag": "Affaissement de la batterie",
    "Thrust lost after 4 minutes of flight": "Poussée perdue après 4 minutes de vol",
    "Horizon Lock": "Verrouillage d'horizon",
    "Gyroflow-style stabilisation of the camera roll (1 = horizon always level)": "Stabilisation du roulis de la caméra façon Gyroflow (1 = horizon toujours droit)",
    "Port": "Port",
    "Show QR Code": "Afficher le QR code",
    "Gravity": "Gravité",
    "Meters per Unit": "Mètres par unité",
    "Real size of one Blender unit. Scale your scene here, not the drone": "Taille réelle d'une unité Blender. Mets ta scène à l'échelle ici, pas le drone",
    "Time Scale": "Échelle de temps",
    "Simulation speed while flying. 0.5 = fly in slow motion, playback is twice as fast":
        "Vitesse de la simulation en vol. 0,5 = vol au ralenti, la lecture est deux fois plus rapide",
    "Speed Multiplier": "Multiplicateur de vitesse",
    "Crosses the scene N times faster with exactly the same handling (rotations, feel). Mouse wheel or + / - while flying, + / - buttons on the phone":
        "Traverse la scène N fois plus vite avec exactement le même pilotage (rotations, sensations). Molette ou + / - en vol, boutons + / - sur le téléphone",
    # collisions
    "Floor": "Sol",
    "Auto (ground under drone)": "Auto (sol sous le drone)",
    "Ray-cast down from the start point to find the ground": "Lancer de rayon vers le bas depuis le départ pour trouver le sol",
    "Fixed Height": "Hauteur fixe",
    "Use Floor Height": "Utiliser la hauteur du sol",
    "Off": "Désactivé",
    "No floor": "Pas de sol",
    "Floor Height": "Hauteur du sol",
    "Collide with Scene": "Collisions avec la scène",
    "Sphere collisions against visible geometry (cached BVH, built only near the drone)":
        "Collisions d'une sphère avec la géométrie visible (BVH en cache, construits seulement près du drone)",
    "Radius (m)": "Rayon (m)",
    "Bounce": "Rebond",
    "Friction": "Frottement",
    "Grip of walls / ground when scraping along them": "Adhérence des murs / du sol quand le drone les frotte",
    "On Impact": "À l'impact",
    "Crash": "Crash",
    "Above Crash Speed the motors cut and the drone tumbles down (real FPV). R to reset":
        "Au-delà de la vitesse de crash, les moteurs coupent et le drone tombe (vrai FPV). R pour repartir",
    "Always bounce off and keep flying": "Toujours rebondir et continuer à voler",
    "Crash Speed (m/s)": "Vitesse de crash (m/s)",
    "Impact speed into a surface that ends the flight in Crash mode": "Vitesse d'impact sur une surface qui termine le vol en mode Crash",
    "Impact Shake": "Secousse à l'impact",
    "Camera shake on impacts": "Tremblement de la caméra aux impacts",
    # camera
    "Camera Uptilt": "Inclinaison de la caméra",
    "Field of View": "Champ de vision",
    "Vibration": "Vibration",
    "Camera jitter driven by throttle (applied to the rig camera)": "Vibration de la caméra selon les gaz (appliquée à la caméra du rig)",
    "Look Through Camera": "Voir à travers la caméra",
    "Switch the 3D view to the drone camera while flying": "Passer la vue 3D sur la caméra du drone pendant le vol",
    # recording
    "Record": "Enregistrer",
    "Bake the flight into keyframes, one key per frame": "Enregistrer le vol en keyframes, une clé par image",
    "Start Frame": "Image de départ",
    "Replace Previous Take": "Remplacer la prise précédente",
    "Delete the drone's existing keys from the start frame before recording": "Supprimer les clés existantes du drone depuis l'image de départ avant d'enregistrer",
    "Play Scene": "Jouer la scène",
    "Advance the scene frame while flying so animated objects move with you": "Avancer l'image de la scène pendant le vol pour que les objets animés bougent avec toi",
    "Extend Frame Range": "Étendre la plage d'images",
    "Countdown": "Décompte",
    # performance
    "Viewport While Flying": "Vue pendant le vol",
    "Fast (Solid)": "Rapide (Solid)",
    "Switch the view to Solid while flying, restore afterwards. Smoothest": "Passer la vue en Solid pendant le vol, puis restaurer. Le plus fluide",
    "Light Preview": "Aperçu léger",
    "Keep Material/Rendered but at lower resolution, volumes simplified": "Garder Material / Rendu mais en basse résolution, volumes simplifiés",
    "Unchanged": "Inchangée",
    "No change (can lag on heavy scenes)": "Aucun changement (peut ramer sur les scènes lourdes)",
    "Hide Volumes": "Masquer les volumes",
    "Hide clouds / smoke volume boxes in the viewport while flying": "Masquer les volumes de nuages / fumée dans la vue pendant le vol",
    "Tile Size (m)": "Taille des tuiles (m)",
    "Min Faces": "Faces min",
    "Only meshes heavier than this are tiled": "Seuls les meshes plus lourds que ça sont découpés",
    "Draw Distance (m)": "Distance d'affichage (m)",
    "Far clipping distance while flying (0 = off): nothing farther is drawn. Free, works on any scene; with Camera Culling, objects that far are also hidden":
        "Distance de coupure en vol (0 = désactivé) : rien n'est affiché au-delà. Gratuit, marche sur toutes les scènes ; avec le Camera Culling, les objets aussi loin sont aussi masqués",
    "While flying, hide what the FPV camera cannot see (and bring it back before it enters the view). Off-screen objects stop being evaluated, drawn and shadowed. Everything is restored when you stop":
        "Pendant le vol, masque ce que la caméra FPV ne voit pas (et le remet avant qu'il entre dans l'image). Les objets hors champ ne sont plus calculés, affichés ni ombrés. Tout est restauré à l'arrêt",
    "Cull": "Masquer",
    "Heavy Objects": "Objets lourds",
    "Animated objects, modifiers / geometry nodes, volumes, collection instances and meshes above Min Faces. Best gain for the cost: recommended":
        "Objets animés, modificateurs / geometry nodes, volumes, instances de collection et meshes au-dessus de Faces min. Meilleur gain pour le coût : conseillé",
    "All Objects": "Tous les objets",
    "Every object. Best for Material / Rendered view (shadows); on scenes with many thousands of objects each change costs a scene update, the batches are then spaced out":
        "Tous les objets. Idéal en vue Material / Rendu (ombres) ; sur des scènes de plusieurs milliers d'objets chaque changement coûte une mise à jour de la scène, les lots sont alors espacés",
    "Margin": "Marge",
    "Extra angle around the camera view kept visible. It also widens automatically with the rotation speed":
        "Angle supplémentaire autour du champ de la caméra gardé visible. Il s'élargit aussi tout seul avec la vitesse de rotation",
    "Keep Around (m)": "Garder autour (m)",
    "Objects this close to the drone are never hidden (behind you, collisions)": "Les objets aussi proches du drone ne sont jamais masqués (derrière toi, collisions)",
    "Heavy Objects: meshes with at least this many faces are culled": "Objets lourds : les meshes d'au moins ce nombre de faces sont masqués",
    "Pause Other Add-ons": "Mettre en pause les autres addons",
    "Detach the other add-ons' frame-change / scene-update handlers while flying (restored at the end). With Play Scene they run on every image: a heavy or buggy one makes the flight stutter or even crash Blender":
        "Détache les handlers « changement d'image / mise à jour de scène » des autres addons pendant le vol (remis à la fin). Avec Jouer la scène ils tournent à chaque image : un addon lourd ou bogué fait saccader le vol, voire planter Blender",
    "Light Overlays": "Overlays allégés",
    "Turn off the costly overlays while flying: statistics (recounted on every image), selection outline, extras, wireframes, cavity, shadows, X-ray...":
        "Désactive les overlays coûteux pendant le vol : statistiques (recomptées à chaque image), contour de sélection, extras, filaire, cavity, ombres, rayons X...",
    "Simplify": "Simplifier",
    "Turn on Simplify (no subdivision, low volume resolution) while flying": "Activer Simplifier (pas de subdivision, volumes en basse résolution) pendant le vol",
    "Update Rate": "Fréquence de mise à jour",
    "Screen Refresh (auto)": "Rafraîchissement de l'écran (auto)",
    "Locked to your monitor's refresh rate (60/144/... Hz): evenly spaced images, no stutter":
        "Calée sur la fréquence de ton écran (60/144/... Hz) : images régulières, sans saccade",
    "30 Hz": "30 Hz",
    "Lightest": "Le plus léger",
    "60 Hz": "60 Hz",
    "Locked 60 Hz": "Calée à 60 Hz",
    "120 Hz": "120 Hz",
    "Locked 120 Hz": "Calée à 120 Hz",
    "144 Hz": "144 Hz",
    "Locked 144 Hz": "Calée à 144 Hz",
    "RC Smoothing (ms)": "Lissage RC (ms)",
    "Filters the stick packets so the drone never moves in steps (0 = auto: 30 ms phone, 40 ms keyboard, 10 ms radio)":
        "Filtre les paquets des sticks pour que le drone ne bouge jamais par à-coups (0 = auto : 30 ms téléphone, 40 ms clavier, 10 ms radio)",
    "OSD": "OSD",
    "Draw the FPV on-screen display": "Afficher l'OSD FPV à l'écran",
    "Live Axes": "Axes en direct",
    # ---------------------------------------------- 2.8
    'FIRE: F / click   %d shots': 'TIR : F / clic   %d tirs',
    'Smooth the recorded take for a cinematic move: calmer path and orientation, camera shake removed. Same timing, undoable': 'Lisser la prise enregistrée pour un mouvement cinéma : trajectoire et orientation plus calmes, vibrations de caméra retirées. Même timing, annulable',
    'Smooth Take': 'Lisser la prise',
    'Re-fly the take from the recorded stick inputs with the CURRENT settings (drone type, rates, wind, speed, collisions...). Edit the inputs in the Graph Editor (FPV Inputs) to change the flight. Undoable': 'Refaire voler la prise à partir des sticks enregistrés avec les réglages ACTUELS (type de drone, rates, vent, vitesse, collisions...). Modifie les entrées dans le Graph Editor (FPV Inputs) pour changer le vol. Annulable',
    'Re-fly Take': 'Revoler la prise',
    'Add (or rebuild) the detailed 3D quad on the drone rig, sized from Frame Size': 'Ajouter (ou reconstruire) le drone 3D détaillé sur le rig, à la taille du châssis',
    'Add Drone Model': 'Ajouter le modèle de drone',
    'Add a practice course at the 3D cursor: a loop of gates, slalom pillars and a dive gap': "Ajouter un parcours d'entraînement au curseur 3D : une boucle de portes, des piliers en slalom et un passage en piqué",
    'Add Practice Course': "Ajouter un parcours d'entraînement",
    'Take smoothed: %d frames': 'Prise lissée : %d images',
    'Take re-flown: %d frames in %.1fs, top speed %.0f km/h': 'Prise revolée : %d images en %.1f s, vitesse max %.0f km/h',
    'Drone model added (%d objects)': 'Modèle de drone ajouté (%d objets)',
    'Practice course added (%d objects, collection FPV_Course)': 'Parcours ajouté (%d objets, collection FPV_Course)',
    '   |   F / click: fire': '   |   F / clic : tir',
    'Drone Presets': 'Préréglages de drone',
    'Controller Presets': 'Préréglages de manette',
    'Save the drone settings (type, physical specs, rates, camera, speed) as a preset, or remove one': 'Enregistrer les réglages du drone (type, caractéristiques, rates, caméra, vitesse) en préréglage, ou en supprimer un',
    'Add Drone Preset': 'Ajouter un préréglage de drone',
    'Save the controller settings (input, axes, inversions, deadband, smoothing) as a preset, or remove one': 'Enregistrer les réglages de la manette (entrée, axes, inversions, zone morte, lissage) en préréglage, ou en supprimer un',
    'Add Controller Preset': 'Ajouter un préréglage de manette',
    'Rates': 'Rates',
    'Betaflight': 'Betaflight',
    'RC Rate / Super Rate / Expo (classic Betaflight rates)': 'RC Rate / Super Rate / Expo (rates Betaflight classiques)',
    'Actual': 'Actual',
    'Center Sensitivity / Max Rate / Expo in deg/s (Betaflight 4.3+ default, easy to read)': 'Sensibilité au centre / vitesse max / expo en °/s (par défaut depuis Betaflight 4.3, facile à lire)',
    'Center Sensitivity': 'Sensibilité au centre',
    'Roll / pitch rotation speed around the centre of the stick (deg/s per full stick)': 'Vitesse de rotation en roulis / tangage autour du centre du stick (°/s pour un stick complet)',
    'Max Rate': 'Vitesse max',
    'Roll / pitch rotation speed at full stick (deg/s)': 'Vitesse de rotation en roulis / tangage stick à fond (°/s)',
    'Softens the centre of the stick': 'Adoucit le centre du stick',
    'Yaw Center Sensitivity': 'Sensibilité au centre (lacet)',
    'Yaw Max Rate': 'Vitesse max (lacet)',
    'Position': 'Position',
    'Smoothing of the path (Gaussian, in seconds). 0 = untouched': 'Lissage de la trajectoire (gaussien, en secondes). 0 = inchangée',
    'Rotation': 'Rotation',
    'Smoothing of the drone orientation (Gaussian, in seconds). Higher = calmer, more cinematic camera': "Lissage de l'orientation du drone (gaussien, en secondes). Plus haut = caméra plus calme, plus cinéma",
    'Camera Shake': 'Vibrations caméra',
    'How much of the recorded camera vibration / impact shake is removed (1 = all, 0 = kept)': "Part des vibrations / secousses d'impact de la caméra retirée (1 = tout, 0 = gardées)",
    'Projectiles': 'Projectiles',
    'Experimental: fire balls from the drone camera while flying (F, left click or the phone button). They bounce on the scene and are recorded with the take': 'Expérimental : tire des balles depuis la caméra du drone en vol (F, clic gauche ou bouton du téléphone). Elles rebondissent sur la scène et sont enregistrées avec la prise',
    'Speed (m/s)': 'Vitesse (m/s)',
    "Muzzle speed, added to the drone's own speed": 'Vitesse de tir, ajoutée à celle du drone',
    'Lifetime (s)': 'Durée de vie (s)',
    'Max Projectiles': 'Projectiles max',
    'Objects kept ready in the FPV_Projectiles collection (the oldest is reused)': 'Objets gardés prêts dans la collection FPV_Projectiles (le plus ancien est réutilisé)',
    'Fire Rate (/s)': 'Cadence (/s)',
    'Drone Model': 'Modèle de drone',
    'Detailed Quad': 'Drone détaillé',
    'A renderable 3D quad (frame, motors, spinning props, battery, camera), sized from Frame Size. For third-person shots': 'Un drone 3D rendu (châssis, moteurs, hélices qui tournent, batterie, caméra), à la taille du châssis. Pour les plans extérieurs',
    'Wire Proxy': 'Proxy filaire',
    'A light wireframe helper, not rendered (the drone is only the camera)': "Un repère filaire léger, non rendu (le drone n'est que la caméra)",
    'This drone has no recorded inputs (record a flight first)': "Ce drone n'a pas d'entrées enregistrées (enregistre d'abord un vol)",
    'No recorded take to smooth (record a flight first)': "Aucune prise à lisser (enregistre d'abord un vol)",
    'Projectiles (experimental)': 'Projectiles (expérimental)',
    'Post-flight smoothing': 'Lissage après le vol',
    'Re-fly from the recorded sticks': 'Revoler à partir des sticks enregistrés',
    'Fire: F or left click while flying, or the phone button': 'Tir : F ou clic gauche en vol, ou le bouton du téléphone',
    'Change the settings, or edit FPV Inputs in the Graph Editor, then:': 'Change les réglages, ou modifie FPV Inputs dans le Graph Editor, puis :',
    'Record a flight first (the sticks are saved with it)': "Enregistre d'abord un vol (les sticks sont sauvegardés avec)",
    # ---------------------------------------------- 2.9 (PS4 / PS5 controller)
    'L2': 'L2',
    'R2': 'R2',
    'Options': 'Options',
    'stop the flight': 'arrêter le vol',
    'Triangle': 'Triangle',
    'back to start': 'retour au départ',
    'Cross': 'Croix',
    'fire (projectiles)': 'tirer (projectiles)',
    'R1 / L1': 'R1 / L1',
    'speed + / -': 'vitesse + / -',
    'Square': 'Carré',
    'recenter (Motion)': 'recentrer (Motion)',
    'Touchpad': 'Pavé tactile',
    'show / hide the OSD': "afficher / masquer l'OSD",
    'PS4 / PS5 controllers are read directly on Windows only (use Pygame elsewhere)': 'Les manettes PS4 / PS5 ne sont lues directement que sous Windows (ailleurs, utilise Pygame)',
    'No PS4 / PS5 controller found: plug it in with USB, or pair it with Bluetooth': 'Aucune manette PS4 / PS5 trouvée : branche-la en USB ou associe-la en Bluetooth',
    "The PS4 / PS5 controller is busy (DS4Windows 'hide' mode?): close it, or use Xbox Gamepad": "La manette PS4 / PS5 est occupée (mode « masquer » de DS4Windows ?) : ferme-le, ou utilise l'entrée Manette Xbox",
    'PS4 / PS5 Controller': 'Manette PS4 / PS5',
    'DualShock 4 or DualSense, USB or Bluetooth, read directly (no DS4Windows or Steam needed). Buttons fly the drone, rumble and light bar on PS4': 'DualShock 4 ou DualSense, en USB ou Bluetooth, lue directement (sans DS4Windows ni Steam). Les boutons pilotent le vol, vibrations et barre lumineuse sur PS4',
    'Light bar: blue waiting, green armed, red recording': 'Barre lumineuse : bleu en attente, vert armé, rouge en enregistrement',
    'No PS4 / PS5 controller found: USB cable or Bluetooth pairing': 'Aucune manette PS4 / PS5 trouvée : câble USB ou association Bluetooth',
    # ---------------------------------------------- 2.9 (altitude hold, triggers)
    'ALT HOLD  %+.1f m/s': 'ALTITUDE  %+.1f m/s',
    'Direct (real FPV)': 'Direct (vrai FPV)',
    'The stick is the motor power, like a real FPV quad': 'Le stick règle la puissance des moteurs, comme un vrai drone FPV',
    'Altitude Hold': "Maintien d'altitude",
    'Stick at the centre = the drone holds its height, up = climbs, down = descends. Easiest with a gamepad': 'Stick au centre = le drone garde son altitude, vers le haut = il monte, vers le bas = il descend. Le plus simple à la manette',
    'Climb Speed (m/s)': 'Vitesse verticale (m/s)',
    'Altitude Hold: vertical speed with the stick fully up / down': "Maintien d'altitude : vitesse de montée / descente stick à fond",
    'Triggers: Forward / Back': 'Gâchettes : avancer / reculer',
    'R2 (RT) flies forward, L2 (LT) backward: the more you press, the faster. Angle mode; combine with Altitude Hold to cruise level': "R2 (RT) fait avancer, L2 (LT) reculer : plus tu appuies, plus ça va vite. Mode Angle ; avec le maintien d'altitude, le drone avance à hauteur constante",
    'Forward Axis': 'Axe avancer',
    'R2 / RT on a PS or Xbox pad': 'R2 / RT sur une manette PS ou Xbox',
    'Backward Axis': 'Axe reculer',
    'L2 / LT on a PS or Xbox pad': 'L2 / LT sur une manette PS ou Xbox',
    "Turn on 'throttle back to centre' in the phone settings": 'Active « Gaz qui reviennent au centre » dans les réglages du téléphone',
    'Triggers work in Angle mode': 'Les gâchettes fonctionnent en mode Angle',
    'R2 axis': 'Axe R2',
    'L2 axis': 'Axe L2',
    # ---------------------------------------------- 2.9 (smooth mode)
    'SMOOTH': 'FLUIDE',
    'Smooth': 'Fluide',
    'Cinematic and easy, less realistic: the sticks set the speed (forward, sideways, up / down, turn), the drone glides, holds its height, stops by itself; level camera, no crash': "Cinéma et facile, moins réaliste : les sticks donnent la vitesse (avancer, glisser, monter / descendre, tourner), le drone glisse, garde son altitude, s'arrête tout seul ; caméra à l'horizontale, pas de crash",
    'Smooth mode: speed with the stick fully forward': "Mode fluide : vitesse stick à fond vers l'avant",
    'Smoothness': 'Douceur',
    'Smooth mode: time to reach the asked speed. Higher = softer starts and stops, more cinematic': 'Mode fluide : temps pour atteindre la vitesse demandée. Plus haut = départs et arrêts plus doux, plus cinéma',
    'Turn Speed (°/s)': 'Vitesse de rotation (°/s)',
    'Smooth mode: rotation speed with the yaw stick fully over': 'Mode fluide : vitesse de rotation stick de lacet à fond',
    'Lean': 'Inclinaison',
    'Smooth mode: how much the drone leans when it speeds up / brakes (0 = never)': 'Mode fluide : à quel point le drone penche quand il accélère / freine (0 = jamais)',
    'Camera Angle': 'Angle de la caméra',
    'Smooth mode: camera pitch, level horizon (0 = straight ahead, negative = looking down)': 'Mode fluide : inclinaison de la caméra, horizon droit (0 = droit devant, négatif = vers le bas)',
    'Smooth mode: the sticks set the speed': 'Mode fluide : les sticks donnent la vitesse',
    'Triggers work in Angle and Smooth modes': 'Les gâchettes fonctionnent en modes Angle et Fluide',
    'Triggers work in Acro, Angle and Smooth modes': 'Les gâchettes fonctionnent en modes Acro, Angle et Fluide',
    'Speed with R2 fully pressed (Acro, Angle and Smooth modes). The pressure sets the speed (half pressed = half the speed), held like a cruise control. Change it in flight with D-pad up / down (or Home / End)': "Vitesse avec R2 enfoncée à fond (modes Acro, Angle et Fluide). La pression règle la vitesse (à moitié = moitié de la vitesse), tenue comme un régulateur. Réglable en vol avec la croix haut / bas (ou Origine / Fin)",
    'R2 (RT) flies forward, L2 (LT) backward: the more you press, the faster. Acro, Angle and Smooth modes; the throttle stick still climbs / descends': "R2 (RT) avance, L2 (LT) recule : plus on appuie, plus ça va vite. Modes Acro, Angle et Fluide ; le stick des gaz monte / descend en même temps",
    # ---------------------------------------------- 2.9.1 (trigger speed)
    'Trigger Max Speed (km/h)': 'Vitesse max gâchette (km/h)',
    'Angle mode: speed with R2 fully pressed. The pressure sets the speed (half pressed = half the speed), held like a cruise control': 'Mode Angle : vitesse avec R2 enfoncée à fond. La pression règle la vitesse (à moitié = moitié de la vitesse), tenue comme un régulateur',
    'R2 %d km/h': 'R2 %d km/h',
    "Smooth mode: R2 fully pressed = the smooth mode's Max Speed": 'Mode fluide : R2 à fond = la vitesse max du mode fluide',
    # ---------------------------------------------- 2.10.0 (smooth camera, R2 power, climb while flying forward)
    'Smooth Camera': 'Caméra fluide',
    "Very fluid camera rotation: the view follows the drone's turns, flips and tilts with a soft, jerk-free delay (all flight modes). The drone itself flies exactly the same": "Rotation de caméra très fluide : la vue suit les virages, flips et inclinaisons du drone avec un léger retard doux, sans à-coups (tous les modes de vol). Le drone, lui, vole exactement pareil",
    'Rotation Smoothness': 'Fluidité de rotation',
    'Smooth Camera: how long the view takes to follow a rotation. Higher = more fluid and cinematic, lower = more direct': 'Caméra fluide : temps que met la vue à suivre une rotation. Plus haut = plus fluide et cinématique, plus bas = plus direct',
    'Level Roll': "Redresser le roulis",
    "Smooth Camera: removes part of the drone's roll from the view (1 = horizon always level, like a gimbal)": "Caméra fluide : retire une partie du roulis du drone de la vue (1 = horizon toujours droit, comme une nacelle)",
    'Power Step (km/h)': 'Pas de puissance (km/h)',
    'R2 power change for each press of D-pad up / down (or Home / End) in flight': 'Changement de puissance R2 à chaque appui sur la croix haut / bas (ou Origine / Fin) en vol',
    'Speed with R2 fully pressed (Angle and Smooth modes). The pressure sets the speed (half pressed = half the speed), held like a cruise control. Change it in flight with D-pad up / down (or Home / End)': "Vitesse avec R2 enfoncée à fond (modes Angle et Fluide). La pression règle la vitesse (à moitié = moitié de la vitesse), tenue comme un régulateur. Réglable en vol avec la croix haut / bas (ou Origine / Fin)",
    'R2 (RT) flies forward, L2 (LT) backward: the more you press, the faster. Angle and Smooth modes; the throttle stick still climbs / descends': "R2 (RT) avance, L2 (LT) recule : plus on appuie, plus ça va vite. Modes Angle et Fluide ; le stick des gaz monte / descend en même temps",
    'In flight: D-pad up / down (or Home / End) = R2 power + / -': 'En vol : croix haut / bas (ou Origine / Fin) = puissance R2 + / -',
    'Throttle stick: climb / descend while R2 flies forward': 'Stick des gaz : monter / descendre pendant que R2 avance',
    'R2 POWER %d km/h': 'PUISSANCE R2 %d km/h',
    '   Home / End: R2 power': '   Origine / Fin : puissance R2',
    'D-pad up / down': 'Croix haut / bas',
    'R2 power + / -': 'puissance R2 + / -',
    'Only R2 Moves Forward': 'Seul R2 fait avancer',
    "With the triggers on: the pitch stick no longer moves the drone forward / backward (no creeping from a drifting stick) and releasing R2 brakes to a stop instead of coasting. Only R2 / L2 fly forward / backward": "Avec les gâchettes : le stick de tangage ne fait plus avancer / reculer le drone (plus de dérive d'un stick usé) et relâcher R2 freine jusqu'à l'arrêt au lieu de glisser. Seules R2 / L2 font avancer / reculer",
    'Bank (Roll)': 'Roulis (pencher)',
    "Tilt the phone sideways: the drone rolls on its side like the phone and slides that way, the camera tilts with it; flying forward it carves a banked turn": "Penche le téléphone sur le côté : le drone roule sur le côté comme le téléphone et glisse de ce côté, la caméra penche avec lui ; en avançant il prend un virage incliné",
    'Tilt the phone left/right like a steering wheel (DJI Motion): flat yaw, level camera': 'Penche le téléphone à gauche / droite comme un volant (DJI Motion) : lacet à plat, caméra à l\'horizontale',
    'Carve': 'Virage',
    "Bank steering: how much the drone also turns when it rolls while flying forward (0 = it only rolls and slides sideways)": "Pilotage en roulis : à quel point le drone tourne aussi quand il penche en avançant (0 = il penche et glisse seulement sur le côté)",
    'Camera Roll': 'Roulis caméra',
    "Bank steering: how much of the drone's roll the camera shows (1 = the horizon tilts exactly like the phone)": "Pilotage en roulis : part du roulis du drone visible à l'image (1 = l'horizon penche exactement comme le téléphone)",
    # ---------------------------------------------- 2.11 (editable curve recording)
    'Turn the recorded take into an editable curve: a few Bezier keys where the motion changes (within the precision settings), rotation as X / Y / Z angles. Undoable': 'Transformer la prise en courbe modifiable : quelques clés de Bézier là où le mouvement change (selon la précision réglée), rotation en angles X / Y / Z. Annulable',
    'Simplify Take': 'Simplifier la prise',
    'Editable curve: %d frames -> %d position keys, %d rotation keys, %d camera keys': 'Courbe modifiable : %d images -> %d clés de position, %d clés de rotation, %d clés de caméra',
    'Editable curve: %d position keys, %d rotation keys, %d camera keys': 'Courbe modifiable : %d clés de position, %d clés de rotation, %d clés de caméra',
    'Keys': 'Clés',
    'Every Frame (exact)': 'Chaque image (exact)',
    'One key per frame: the flight exactly as flown': "Une clé par image : le vol exactement tel qu'il a été piloté",
    'Editable Curve': 'Courbe modifiable',
    'A few Bezier keys where the motion changes, within the precision below: easy to edit in the Graph Editor (rotation as X / Y / Z angles)': 'Quelques clés de Bézier là où le mouvement change, selon la précision ci-dessous : facile à retoucher dans le Graph Editor (rotation en angles X / Y / Z)',
    'Position Precision (m)': 'Précision de position (m)',
    'Editable Curve: how far the curve may move away from the flown path. Higher = fewer keys, smoother': 'Courbe modifiable : écart permis entre la courbe et la trajectoire pilotée. Plus haut = moins de clés, plus lisse',
    'Rotation Precision': 'Précision de rotation',
    'Editable Curve: how far the rotation may move away from the flown one. Higher = fewer keys, calmer camera': 'Courbe modifiable : écart permis sur la rotation. Plus haut = moins de clés, caméra plus calme',
    'Key at Least Every (frames)': 'Une clé au moins toutes les (images)',
    'Editable Curve: also put a key at least every N frames (0 = only where needed)': "Courbe modifiable : ajoute aussi une clé au moins toutes les N images (0 = seulement là où c'est utile)",
    'Keys of the take': 'Clés de la prise',
}
