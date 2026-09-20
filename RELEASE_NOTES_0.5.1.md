# DocSpecBridge 0.5.0 — Release notes

### Jira discovery refinement

Interactive Jira extraction now offers an explicit choice between entering an issue key directly and browsing Jira. Browse mode follows project → issue type → paginated/searchable issue list, so large projects are filtered by issue type before any issue page is displayed.


## Added

- Interactive navigation reorganized around **Extract**, **Import** and **RAG**.
- Native XLSX adapter with workbook summary, one child package/page per worksheet, formula metadata/cached-result diagnostics and common chart SVG previews.
- Jira project search/listing and project-specific issue-type discovery.
- Jira extraction of comments and attachments, plus optional changelog.
- Resumable Jira publication through `jira_publication_state.json`.
- Jira attachment upload and ADF image reinsertion at canonical image positions.
- Selectable Confluence attachment extraction (`none`, `images`, `all`) and optional package ZIP.
- Optional Confluence auxiliary publication attachments (`source`, `rendered`, `all`) and package ZIP.
- Global repeatable `--set dotted.path=value` configuration override and `config-keys` discovery command.

## Changed

- Human-facing package outputs are source-named (`specification.md`, `specification.json`, `specification.html`, `specification.rag.md`, `specification.confluence.md`).
- Package manifest schema is now `0.5`.
- Jira export includes comments by default.
- XLSX is part of the default extraction extensions.
- `--render-mermaid` now targets `confluence.converter.render_mermaid`.

## Compatibility

- 0.4.x packages remain readable via manifest resolution and fallbacks to `document.json`, `document.md`, `document.rag.md`, `document.html` and `render_document.md`.
- Legacy CLI command names are retained.
- Legacy YAML migration remains enabled.

## Correctifs de validation

- Ajout de `docspecbridge --version` et de l’alias `docspecbridge -V`.
- Contrainte Pillow alignée sur Python 3.14 : `Pillow>=12,<13`.

## Correctifs interactifs après validation utilisateur

- Tous les nouveaux menus et prompts 0.5.0 sont localisés dans les cinq langues supportées (`fr`, `en`, `de`, `es`, `zh`).
- `Extract > Confluence` réutilise le navigateur Confluence complet : instance -> espace -> page, au lieu d'exiger un `page_id` brut.
- Les valeurs Confluence par défaut ne court-circuitent plus la découverte : elles sont affichées et simplement pré-sélectionnées dans les listes.
- `Extract > Jira` découvre et fait sélectionner un projet avant la saisie de la clé du ticket.
- `Import > Jira` réutilise le même sélecteur de projet puis découvre les types de tickets du projet.
- Nouveau sous-menu `Paramétrage > Jira Cloud` : instances effectives, instance par défaut, projet par défaut et type de ticket par défaut avec découverte Jira.
- Les instances Jira explicites se combinent désormais avec les instances Atlassian héritées de Confluence ; une surcharge Jira n'efface plus les autres instances héritées.
- `Esc` annule l'action interactive en cours et revient au menu précédent, y compris pendant les saisies texte. Les modifications partielles d'une action de paramétrage sont restaurées lors de l'annulation.

## Correctifs Jira après validation utilisateur

- Extraction des pièces jointes fiabilisée via l'endpoint Jira Cloud dédié `attachment/content/{id}` au lieu de dépendre uniquement de l'URL `content` renvoyée dans les métadonnées.
- Les images Jira sont ré-associées aux fichiers locaux également par nom/`alt`, ce qui couvre les ADF dont l'identifiant media diffère de l'identifiant numérique de pièce jointe.
- Les liens vers pièces jointes sont réécrits vers les fichiers locaux lorsqu'une correspondance est trouvée.
- Toutes les pièces jointes téléchargées restent visibles dans une section localisée `Pièces jointes / Attachments`, y compris lorsqu'elles n'étaient pas inline dans la description.
- Les commentaires paginés sont rendus dans une section localisée et un fallback utilise les commentaires inclus dans le ticket si nécessaire.
- `Extract > Jira` dispose maintenant d'un navigateur de tickets : page récente limitée (50 par défaut), recherche/filtre, page suivante, saisie directe d'une clé et `Esc`. Aucun chargement exhaustif du projet n'est effectué.
- Nouvelle commande `jira-issues --project KEY [--query texte] [--limit N]`.
- Nouveau paramètre `jira.discovery.page_size` (défaut 50, maximum 100).

### Correctifs de stabilisation supplémentaires

- Migration automatique de la liste d'extensions standard 0.4.x afin d'activer réellement le nouveau support `.xlsx` sans écraser une liste d'extensions personnalisée.
- Extraction des commentaires Jira renforcée : fusion/déduplication de l'API Jira Platform, des commentaires embarqués dans le ticket et, pour Jira Service Management ou en fallback, de l'API `servicedeskapi`.
- Écriture systématique de `<ISSUE>.comments.json` lorsque l'extraction des commentaires est activée, même si la liste est vide, avec diagnostics lorsqu'un ticket annonce des commentaires non accessibles au compte API.
