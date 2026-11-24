# TAKUMI FPV

Simulateur FPV autonome pour Windows. On ouvre une scène Blender, on la survole à la radio avec son animation, puis on renvoie le mouvement de caméra dans Blender en **Alembic (`.abc`)** ou en **fichier de prise (`.json`)**.

## Utilisation
1. Télécharge `TAKUMI-FPV-x.y.z-win-x64.zip` dans les [releases](https://github.com/lburdier/fpv-drone/releases), dézippe-le et lance `TAKUMI FPV.exe`. Il n'y a rien à installer.
2. **Ouvre le `.blend`**, avec le bouton ou en le glissant dans la fenêtre. Blender exporte la scène en arrière-plan, une seule fois : le résultat est gardé dans `TAKUMI_FPV/` à côté du `.blend` et n'est refait que si le fichier change. Une scène déjà exportée avec l'addon (*Exporter la scène pour le logiciel*) s'ouvre aussi, sans Blender.
3. **Radio** : branche-la en USB en mode *USB Joystick (HID)* (EdgeTX / OpenTX). Chaque voie peut être détectée (◎), inversée (⇅) et calibrée. Le clavier marche aussi pour tester.
4. **Vole** :
   - `T` lance un vol d'entraînement ;
   - `Entrée` lance un vol enregistré : décompte, puis le temps et l'animation démarrent au premier mouvement des sticks ;
   - `R` recommence la prise ;
   - `Échap` arrête.
5. **Prises** : elles sont enregistrées dans `PRISES/`, à raison d'une position et d'une rotation par image, en coordonnées monde Blender. Si Blender est installé, l'Alembic `.abc` est créé automatiquement.

## Retour dans Blender
- **Alembic** : *Fichier › Importer › Alembic (.abc)*.
- **Prise `.json`** : addon FPV Drone, panneau *Enregistrement*, bouton *Importer une prise du logiciel*.

Les deux méthodes donnent la même caméra : même position, même rotation et même focale à chaque image.

## Physique
Le modèle de vol est celui de l'addon FPV Drone :
- rates Betaflight, modes Acro et Angle ;
- inertie des moteurs, prop-wash, traînée, turbulence, effet de sol ;
- simulation à 500 Hz ;
- collisions avec la scène (BVH).

L'échelle (mètres par unité Blender) est reprise du réglage de l'addon.

## Développement
```
cd takumi-fpv
npm install
npm start          # build + lancement
npm run package    # exe dans out/
```
- `blender/takumi_bridge.py` est copié depuis `../appbridge.py` à chaque build.
- Autotest : `electron . --selftest <scene.blend|.json> <dossier>`. Il charge la scène, fait des captures, un vol scripté enregistré, une prise et un `.abc`.

Licence GPL-3.0-or-later.
