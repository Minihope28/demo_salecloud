# Dossier client — préparation Salesforce

Application web interne qui évite au commercial de ressaisir à la main, écran par écran, les informations reçues sur WhatsApp.

**Le commercial :**

1. dépose le **RC** et, s'il l'a, le **document fiscal** (PDF) ;
2. **vérifie** les informations lues automatiquement (chaque valeur indique sa source : document et page) ;
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

## Brancher une vraie org Salesforce (mode `live`)

À faire d'abord dans une **sandbox**, jamais directement en production.

1. **Application connectée** (admin Salesforce) : créer une *Connected App* ou une *External Client App* avec OAuth activé,
   URL de rappel = `https://<adresse-de-l-app>/auth/callback`, scopes `api`, `refresh_token`, `id`, **PKCE requis**.
2. **Correspondance des champs** : compléter [`config/field_mapping.json`](config/field_mapping.json).
   Les noms se terminant par `__c` (`RC_Number__c`, `ICE__c`, `Segmentation__c`…) sont des **hypothèses** à remplacer par les vrais noms d'API de l'org Volvo (Setup → Object Manager). `null` = ne pas envoyer le champ.
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
| `app/extraction.py` | Lecture des PDF et proposition de valeurs, avec leur source |
| `app/salesforce.py` | Validation, construction de la requête Composite, client REST réel et org simulée |
| `app/submission.py` | Envoi « tout ou rien », reprise sans doublon après erreur, envoi des pièces jointes |
| `app/auth.py` | Connexion OAuth 2.0 (web server + PKCE) avec le compte Salesforce du commercial |
| `app/drafts.py` | Dossiers en cours (interrompre / reprendre) |
| `app/main.py` | API web |
| `app/static/` | Interface commerciale |
| `config/field_mapping.json` | Correspondance champs de l'application → champs Salesforce |
| `scripts/generate_samples.py` | Génère les PDF fictifs de `samples/` |

## Limites connues

- **Lecture des PDF** : les libellés recherchés sont des hypothèses sur la structure des RC et documents fiscaux marocains. Ils doivent être **calibrés sur un échantillon de vrais documents** (anonymisés) avant le pilote.
- **Documents scannés** : pas d'OCR dans cette version ; ils sont signalés et la saisie reste manuelle.
- **VSS4** : non connecté (étape manuelle depuis l'opportunité).
- **WhatsApp** : non connecté ; le commercial télécharge les pièces puis les dépose.
