# FPV Drone Pilot

Simulateur FPV autonome pour Windows, avec l'interface de Blender. On ouvre une scène Blender, on la survole à la radio avec son animation, puis on renvoie le mouvement de caméra dans Blender sous trois formes : **Alembic (`.abc`)**, **`.blend`** et **fichier de prise (`.json`)**.

Anciennement *TAKUMI FPV*. Ses anciens fichiers restent lisibles.

## Utilisation
1. Télécharge `FPV-Drone-Pilot-x.y.z-win-x64.zip` dans les [releases](https://github.com/lburdier/fpv-drone/releases), dézippe-le et lance `FPV Drone Pilot.exe`. Il n'y a rien à installer.
2. **Ouvre le `.blend`**, avec le bouton ou en le glissant dans la fenêtre. Blender exporte la scène en arrière-plan, une seule fois : le résultat est gardé dans `FPV_PILOT/` à côté du `.blend` et n'est refait que si le fichier change. Une scène déjà exportée avec l'addon (*Exporter la scène pour le logiciel*) s'ouvre aussi, sans Blender.
3. **Radio** : branche-la en USB en mode *USB Joystick (HID)* (EdgeTX / OpenTX). Chaque voie peut être détectée (◎), inversée (⇅) et calibrée. Le clavier marche aussi pour tester.
4. **Vole** :
   - `T` lance un vol d'entraînement ;
   - `Entrée` lance un vol enregistré : décompte, puis le temps et l'animation démarrent au premier mouvement des sticks ;
   - `R` recommence la prise ;
   - `Échap` arrête.
5. **Prises** : elles sont enregistrées dans `PRISES/`, à raison d'une position et d'une rotation par image, en coordonnées monde Blender. Si Blender est installé, le `.abc` et le `.blend` sont créés automatiquement.

## Retour dans Blender
| Format | Dans Blender |
|---|---|
| `.abc` | *Fichier › Importer › Alembic (.abc)* |
| `.blend` | *Fichier › Ajouter (Append) › prise.blend › Object › la caméra* |
| `.json` | addon FPV Drone, panneau *Enregistrement*, *Importer une prise du logiciel* |

Les trois donnent la même caméra : même position, même rotation et même focale à chaque image, avec une clé par image.

Compatibilité :
- `.abc` et `.json` : testés de Blender 4.3 à 5.3 ;
- `.blend` : il est écrit au format Blender 5, lisible à partir de Blender 4.5 LTS (vérifié de 5.0 à 5.3). Pour une version plus ancienne, prends le `.abc`.

## Caméra fluide, sans secousse
- **Lissage des sticks** : la radio est lue à chaque image affichée, la physique tourne à 500 Hz. Entre deux lectures, les sticks sont interpolés, puis filtrés comme le *RC smoothing* de Betaflight (de 80 Hz à 10 Hz, 39 Hz par défaut). Il n'y a plus d'à-coups en escalier.
- **Secousses (vent, prop-wash)** : réglées à 0 par défaut. Le drone garde son inertie, l'inertie des moteurs et sa traînée (ce qu'on ressent sur un vrai drone), mais sans vibration aléatoire. Les chocs n'envoient plus de coup aléatoire dans la caméra.
- **Image interpolée** : chaque image affichée montre le drone à l'instant exact de l'affichage, entre deux pas de physique. Il n'y a pas de micro-saccade.
- **Stabilisation de la prise** : la rotation enregistrée est lissée sans retard, avec un lissage centré façon Gyroflow (jusqu'à 300 ms, 105 ms par défaut). La première et la dernière image ne bougent pas, et le vol brut reste dans la prise (`frames_raw`).

Banc de mesure `test/smoothness.mjs`, même pilote et même radio (bruit de ±0,4 %) :

| | Avant | Maintenant |
|---|---|---|
| Accélération angulaire de la caméra (RMS) | 272 °/s² | 82 °/s² |
| Saut de vitesse de rotation d'une image à l'autre (95ᵉ centile) | 8,6 °/s | 1,2 °/s |
| Irrégularité du temps affiché | 2 ms | 0 ms |
| Stick → 50 % de la rotation | 39 ms | 45 ms |

## Scènes lourdes
Testé sur une ville de 53 km : 4 138 objets, 2,7 millions de triangles, 1 563 animations, `.blend` de 233 Mo.
- **Export** : la première ouverture prend environ 3 minutes, puis la scène est réutilisée telle quelle. Les textures de l'aperçu sont limitées à 1024 px.
- **Au chargement** :
  - les objets qui ne bougent pas sont regroupés par matériau et par zone ;
  - les objets répétés sont instanciés ;
  - les pistes d'animation constantes sont retirées ;
  - les animations sont réunies en une seule.
- **Fidélité à Blender** :
  - caméras de départ prises à la première image du plan, même animées ou attachées à un drone ;
  - matériaux sans shader affichés avec leur couleur d'affichage Blender ;
  - toute la scène visible jusqu'à l'horizon (profondeur inversée, de 2 cm à plusieurs dizaines de km).
- **Qualité Auto** : la résolution baisse si l'image passe sous 50 images par seconde, puis remonte.
- **Carte graphique** : celle de l'écran par défaut. Sur un portable, la carte dédiée doit recopier chaque image vers la puce intégrée. Sur PC fixe, choisis *Dédiée* dans la section Caméra.

Mesuré sur un portable (Ryzen, Radeon intégrée, écran 120 Hz, RTX 3050 Ti) :

| Scène | Images par seconde | Image médiane | 95ᵉ centile |
|---|---|---|---|
| CAM_01 (1,2 M triangles) | 119 | 8,3 ms | 8,5 ms |
| Ville de 53 km (2,7 M triangles, jusqu'à l'horizon) | 65, résolution auto à 70 % | 16,6 ms | 25 ms |

## Physique
Le modèle de vol est celui de l'addon FPV Drone :
- rates Betaflight, modes Acro et Angle ;
- inertie des moteurs, traînée, effet de sol ;
- prop-wash et turbulence, en option ;
- simulation à 500 Hz ;
- collisions avec la scène (BVH).

L'échelle (mètres par unité Blender) est reprise du réglage de l'addon.

## Développement
```
cd fpv-pilot
npm install
npm start          # build + lancement
npm run package    # exe dans out/
```
- `blender/fpv_pilot_bridge.py` est copié depuis `../appbridge.py` à chaque build.
- Autotest : `electron . --selftest <scene.blend|.json> <dossier>`. Il charge la scène, fait des captures, un vol scripté enregistré, une prise, un `.abc` et un `.blend`.
- Banc de fluidité : `npx esbuild test/smoothness.mjs --bundle --platform=node --outfile=out/smoothness.cjs && node out/smoothness.cjs`.

Licence GPL-3.0-or-later.
