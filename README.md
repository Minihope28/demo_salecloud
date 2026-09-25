# Dossier client — préparation Salesforce

Application web interne qui évite au commercial de ressaisir à la main, écran par écran, les informations reçues sur WhatsApp.

**Le commercial :**

1. dépose le **RC** et, s'il l'a, le **document fiscal** (PDF) ;
2. **vérifie** les informations lues automatiquement, présentées **comme dans le formulaire « Nouveau compte » de Salesforce** (chaque valeur indique sa source : document, page, OCR ou déduction). Une information absente ou illisible reste **vide** pour être saisie à la main ;
   - l'adresse est découpée en **rue / code postal / ville** ; le **code postal est vérifié** par rapport à la ville ; le pays est toujours **Maroc** ;
3. vérifie que le client **n'existe pas déjà** dans Salesforce (même RC, même ICE ou nom proche) ;
4. complète le **contact**, la **segmentation** et le **besoin** (opportunité) ;
5. clique sur **« Créer dans Salesforce »**.

**L'application crée en une seule fois** le compte, la segmentation, le contact, l'opportunité et le rôle du contact sur l'opportunité, puis joint les PDF au compte.
Il ne reste au commercial qu'à **ouvrir l'opportunité** et à lancer **« Créer/afficher un devis VSS »** (étape VSS4 manuelle).

> Proposition métier complète (problème, parcours avant/après, architecture, sécurité, ce que Volvo doit fournir, profils, déploiement) : **[docs/proposition.md](docs/proposition.md)**.

---

## Essayer la démonstration (sans Salesforce)

En mode `mock`, Salesforce est **simulé** : rien n'est envoyé nulle part. Les exemples de documents fournis dans `samples/` sont **entièrement fictifs**.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:create_app --factory --port 8000
```

Ouvrir <http://localhost:8000>, puis : **Nouveau dossier → Exemple fictif** (RC puis document fiscal) → **Rechercher dans Salesforce** → cocher la vérification → **Continuer** → compléter le contact et le besoin → **Vérifier le dossier** → **Créer dans Salesforce**.

À tester aussi :
- **Enregistrer et quitter** puis **Reprendre** (interruption par un appel) ;
- recherche de doublon : saisir le numéro RC `55555` → le compte fictif existant est proposé avec « même numéro RC » ;
- **Coller un message WhatsApp** : le téléphone et l'e-mail sont repérés.

## Lire les PDF « aux caractères bizarres » ou scannés : installer l'OCR (recommandé)

Certains PDF (dont des RC) s'affichent correctement mais leur texte interne est **mal encodé** : une lecture directe donne des caractères du type `ΔjɰγΗϟ`. L'application **détecte ces valeurs et ne les propose jamais** (le champ reste vide, marqué « illisible dans le PDF »). Pour les **lire quand même**, elle peut relire la page comme une image avec **Tesseract** (OCR), s'il est installé :

- **Windows** : installer Tesseract (la version Windows la plus répandue est maintenue par UB Mannheim : <https://github.com/UB-Mannheim/tesseract/wiki>) ; pendant l'installation, dans « Additional language data », cocher **French**. L'application le trouve automatiquement dans `C:\Program Files\Tesseract-OCR\` ; sinon, renseigner `TESSERACT_CMD`. Sur un PC d'entreprise, l'installation peut nécessiter l'IT.
- **Serveur / Docker** : déjà inclus dans le `Dockerfile`.

Redémarrer l'application après l'installation. Les valeurs lues par OCR portent la mention « OCR » : à vérifier attentivement.

## Codes postaux : charger le référentiel officiel

Pour déduire la ville, proposer le code postal et vérifier leur cohérence, l'application utilise la liste officielle de **Barid Al-Maghrib**, publiée sur le portail Open Data du Maroc : jeu de données **« Codes postaux des localités »** (<https://data.gov.ma/data/fr/dataset/codes-postaux-des-localites>).

1. Télécharger le fichier (CSV ou Excel) ;
2. l'enregistrer sous `config/codes_postaux.csv` (ou `config/codes_postaux.xlsx`) ;
3. redémarrer l'application : le bas de la section « Adresse » indique le nombre de localités chargées.

Les colonnes sont reconnues d'après leur en-tête (code postal, localité/ville/commune, province, région). Je n'ai pas pu consulter ce fichier : si ses colonnes ne sont pas reconnues, l'application l'indique et il faudra adapter `app/address.py`.
Sans ce fichier, seules des vérifications simples sont faites (code à 5 chiffres, ville déduite de la fin de l'adresse). La démonstration utilise `samples/codes_postaux_demo.csv`, qui ne contient que des **localités fictives**.

## Brancher une vraie org Salesforce (mode `live`)

À faire d'abord dans une **sandbox**, jamais directement en production.

1. **Application connectée** (admin Salesforce) : créer une *Connected App* ou une *External Client App* avec OAuth activé,
   URL de rappel = `https://<adresse-de-l-app>/auth/callback`, scopes `api`, `refresh_token`, `id`, **PKCE requis**.
2. **Correspondance des champs** : compléter [`config/field_mapping.json`](config/field_mapping.json).
   Les noms se terminant par `__c` (`RC_Number__c`, `ICE__c`, `Sole_Proprietorship__c`, `Tax_Id_Type__c`, `Common_Name__c`, `Segmentation__c`…) sont des **hypothèses** à remplacer par les vrais noms d'API de l'org Volvo (Setup → Object Manager). `null` = ne pas envoyer le champ. Les listes de sélection (« Entreprise individuelle », « Type de numéro d'identification fiscale ») se traduisent via `value_maps` si leurs valeurs d'API diffèrent des libellés affichés. Le pays est envoyé via `defaults` (`BillingCountryCode = MA`, à ajuster selon la configuration pays/région de l'org).
3. **Variables d'environnement** : voir [`.env.example`](.env.example) (`APP_MODE=live`, `SESSION_SECRET`, `SF_LOGIN_URL`, `SF_CLIENT_ID`, `SF_REDIRECT_URI`…).
4. **Contrôle automatique de la configuration** : une fois connecté, ouvrir `/api/salesforce/check-mapping`.
   L'application interroge Salesforce et liste, par objet, les champs inexistants, non modifiables, ou obligatoires mais non renseignés.

Chaque commercial se connecte avec **son propre compte Salesforce** : les fiches sont créées à son nom, avec ses droits, ses règles de validation et les règles de doublons de l'org.

## Hébergement

```bash
docker build -t dossier-client .
docker run --env-file .env -p 8000:8000 -v dossier-data:/data dossier-client
```

À placer derrière le reverse proxy HTTPS de Volvo. La version actuelle garde les sessions en mémoire et les brouillons sur disque : **une seule instance** pour le pilote (voir « Passage à l'échelle » dans la proposition).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Les tests couvrent la lecture des PDF, la construction des requêtes Salesforce, le parcours complet en démonstration et le mode live contre une fausse API Salesforce (connexion OAuth, doublon refusé par Salesforce, coupure réseau puis nouvel essai sans double création, jeton expiré renouvelé, échappement des requêtes SOQL).

## Organisation du code

| Fichier | Rôle |
|---|---|
| `app/extraction.py` | Lecture des PDF (pypdf + PDFium, OCR Tesseract si besoin), détection des caractères illisibles, proposition de valeurs avec leur source |
| `app/address.py` | Découpage de l'adresse, ville, code postal, vérification avec le référentiel Barid Al-Maghrib |
| `app/salesforce.py` | Validation, construction de la requête Composite, client REST réel et org simulée |
| `app/submission.py` | Envoi « tout ou rien », reprise sans doublon après erreur, envoi des pièces jointes |
| `app/auth.py` | Connexion OAuth 2.0 (web server + PKCE) avec le compte Salesforce du commercial |
| `app/drafts.py` | Dossiers en cours (interrompre / reprendre) |
| `app/main.py` | API web |
| `app/static/` | Interface commerciale |
| `config/field_mapping.json` | Correspondance champs de l'application → champs Salesforce |
| `scripts/generate_samples.py` | Génère les PDF fictifs de `samples/` et le PDF « mal encodé » de test |

## Limites connues

- **Lecture des PDF** : les libellés recherchés sont des hypothèses sur la structure des RC et documents fiscaux marocains. Ils doivent être **calibrés sur un échantillon de vrais documents** (anonymisés) avant le pilote.
- **Documents scannés ou mal encodés** : lus par OCR si Tesseract est installé, sinon signalés (saisie manuelle). La qualité de l'OCR dépend de la qualité du document.
- **Codes postaux** : le référentiel officiel doit être téléchargé et placé dans `config/` (voir plus haut).
- **VSS4** : non connecté (étape manuelle depuis l'opportunité).
- **WhatsApp** : non connecté ; le commercial télécharge les pièces puis les dépose.
