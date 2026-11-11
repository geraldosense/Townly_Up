# Townly Up

![Townly Up](static/img/logo.svg)

Protótipo de aplicação web para os munícipes do Fundão comunicarem problemas no espaço
público e para uma central municipal **simulada** os acompanhar até à resolução.

> PAP · 12.º ano · Técnico de Desenvolvimento de Software. Demonstração académica — não é
> um serviço oficial da Câmara Municipal do Fundão. Usa apenas dados fictícios.

## Tecnologias

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.11+ · Flask |
| Base de dados | **PostgreSQL** (psycopg 3) · SQLite como alternativa sem instalação · migrações versionadas |
| Imagens | Pillow (validação do conteúdo e remoção de metadados EXIF/GPS) |
| Frontend | HTML · Jinja2 · Bootstrap 5 · Leaflet + OpenStreetMap · Source Serif 4 / Source Sans 3 |
| Testes | pytest |

## Instalar e executar (macOS, no terminal do Cursor)

```bash
cd ~/PAP_2026/smartfundao
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

flask --app app create-admin   # cria a conta da Câmara (pede nome, email e palavra-passe)
flask --app app run --debug --port 8000   # abrir http://127.0.0.1:8000
```

Ou, mais simples: **duplo clique em `iniciar.command`** (instala na 1.ª vez e abre o navegador).

### PostgreSQL

1. Instalar a [Postgres.app](https://postgresapp.com) (arrastar para Aplicações, abrir, carregar em **Initialize**).
2. Duplo clique em `iniciar.command`: cria a base `townlyup`, aplica as migrações e os dados de referência (freguesias, entidades, categorias).

Sem PostgreSQL a aplicação usa SQLite (`instance/townlyup.db`) — o código é o mesmo.
Para escolher à mão: `export DATABASE_URL=postgresql://$USER@localhost:5432/townlyup`.

> No macOS a porta 5000 está ocupada pelo AirPlay Receiver — por isso usamos a 8000.

**Sem dados fictícios**: a base começa vazia (só as 23 freguesias, as entidades e as categorias).
Os munícipes registam-se no site; a 1.ª conta da Câmara cria-se com `create-admin` (ou, online, com
`ADMIN_EMAIL`/`ADMIN_PASSWORD`) e é ela que cria, no site, as contas das Juntas e dos serviços.

Outros comandos:

```bash
flask --app app init-db        # só cria as tabelas e categorias
flask --app app create-admin   # cria um administrador (nunca pelo registo público)
python -m pytest -v            # corre os testes automáticos
```

Para recomeçar do zero: apagar `instance/townlyup.db` (ou `dropdb townlyup`) e o conteúdo de `uploads/`.

Em produção, definir a variável `SECRET_KEY` com um valor aleatório
(`python -c "import secrets; print(secrets.token_hex(32))"`) e `SESSION_COOKIE_SECURE=1` com HTTPS.

## Estrutura

```
app.py            rotas, erros, comandos (create_app)
services.py       regras de negócio: validação, tickets, estados, histórico
security.py       passwords (hash), sessão, perfis, CSRF
db.py             ligação PostgreSQL/SQLite, transações, migrações
reference.py      dados de referência: 23 freguesias (CAOP), Câmara, Juntas, serviços, categorias
geo.py            em que freguesia fica um ponto (limites oficiais CAOP)
migrations/       postgres/ e sqlite/ — tabelas, CHECK, chaves estrangeiras, índices, triggers
templates/        páginas Jinja2 (citizen/, admin/, errors/)
static/           css/style.css · js/map.js · js/new_incident.js · img/logo.svg
uploads/          fotografias (fora de static/, servidas só a quem tem permissão)
tests/            test_flow · test_community · test_entities (+ adaptador PostgreSQL)
static/img/freguesias/   imagens de fundo de cada freguesia (ver abaixo)
```

## Requisitos implementados

| Req. | Onde |
|---|---|
| RF01 Conta, login, logout | `/register`, `/login`, `/logout` |
| RF02 Dois perfis / rotas restritas | `security.role_required` |
| RF03 Título, descrição, categoria | `services.create_incident` |
| RF04 Até 3 imagens válidas | `services.process_image` |
| RF05 Ponto no mapa (+ coordenadas manuais) | `static/js/map.js` |
| RF06 Ticket `TU-AAAA-NNNN` (prefixo do nome Townly Up) | `services.create_incident` |
| RF07 Lista e detalhe dos próprios tickets | `/citizen`, `/incidents/<id>` |
| RF08 Painel com pesquisa e filtros | `/admin` |
| RF09 Estado com data, autor e observação | `services.change_status` |
| RF10 Histórico imutável | `status_history` + triggers em `migrations/` |
| RF11 Arquivar sem apagar | vista «Histórico»/«Arquivo» + trigger |
| RF12 (extra) Contagens por estado e categoria | `/admin` |
| Extra | Notificações na app, mapa de ocorrências no painel, fotografia de resolução, prioridade e serviço responsável |

## Fluxo de estados

```
Registada → Em análise → Em tratamento → Resolvida
     └──────────┴──→ Rejeitada (com motivo) | Duplicada (com ticket de referência)
```

## Segurança (RNF02–RNF05)

- Passwords com hash (Werkzeug), mensagem de login neutra.
- Token CSRF em todos os formulários POST.
- O registo público cria sempre «munícipe»; administradores só por comando.
- Cada munícipe só vê os seus tickets e fotografias (403 para os de outros).
- Uploads: tamanho máximo, tipo verificado pelo conteúdo, nome aleatório, EXIF removido.
- Queries SQL sempre com parâmetros `?`; páginas de erro sem stack traces.

## Testes de aceitação

`python -m pytest -v` cobre TA01–TA08, segurança, comunidade, entidades e restrições da base de dados
(100 testes). Com `TEST_DATABASE_URL=postgresql://…` os mesmos testes correm em PostgreSQL.
TA08 (smartphone) deve também ser confirmado manualmente no telemóvel ou nas
ferramentas de programador do navegador.

## Funcionalidades estilo Waze

| Funcionalidade | Onde |
|---|---|
| **Mapa vivo** público (só dados seguros: categoria, estado, nº de confirmações) | `/mapa`, `static/js/live.js` |
| **«Eu também vi»** e **«Já foi resolvido?»** — cada munícipe confirma uma vez | `community.confirm`, tabela `confirmations` |
| **Comunicar em 3 passos** (categoria → foto → local por GPS) | `templates/citizen/new_incident.html`, `static/js/new_incident.js` |
| **Aviso de duplicados** a menos de 50 m (RF14) | `community.nearby` (fórmula de Haversine) |
| **Pontos, níveis e ranking** por munícipe e por freguesia | `community.py`, tabela `points_log`, `/ranking` |
| **PWA** — instalar no ecrã do telemóvel, página offline | `static/manifest.webmanifest`, `static/sw.js` |

Pontos: comunicar +10 · «Eu também vi» +2 · «Já foi resolvido?» +1 · a sua ocorrência foi confirmada +1 · a sua ocorrência foi resolvida +15.
Níveis: Olho novo (0) → Olho atento (30) → Vigilante do bairro (100) → Guardião da cidade (250) → Embaixador Townly (500).

## Mapa das freguesias (limites oficiais CAOP / DGT)

- Página inicial: «O território do Fundão» — as 23 freguesias desenhadas em SVG (funciona sem internet). Ao passar o cursor a freguesia fica realçada e mostra o nome e as ocorrências ativas; a cor fica mais verde onde há mais ocorrências; um clique abre o mapa vivo filtrado.
- Mapa vivo: camada «Freguesias» (ligar/desligar), nome ao passar o cursor, clique filtra a lista; `/mapa?freguesia=<DICOFRE>`.
- Os limites ficam em `static/data/freguesias_fundao.geojson`. Para os obter: `flask --app app freguesias` (o `iniciar.command` faz isto sozinho). Fonte: Carta Administrativa Oficial de Portugal (CAOP 2022), Direção-Geral do Território.
- Sem esse ficheiro, a secção simplesmente não aparece — o resto da aplicação funciona.

## API JSON v1 (base para as futuras apps Android/iOS)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/v1/categories` | Categorias com ícone e cor |
| GET | `/api/v1/map` | Ocorrências do mapa público |
| GET | `/api/v1/incidents/nearby?lat=&lng=&category_id=` | Ocorrências ativas a menos de 50 m |
| POST | `/api/v1/incidents/<id>/confirm` | `{"kind": "seen" \| "fixed"}` (sessão + cabeçalho `X-CSRF-Token`) |
| GET | `/api/v1/me` | Pontos e nível do utilizador |

Próximo passo para as apps das lojas: autenticação por token (ex.: JWT) e upload de fotografias pela API.

## Identidade visual

- Logótipo: `static/img/logo.svg` (olho + marcador + anel) — também usado como favicon.
- Cores: azul-marinho `#0b2a5b` (marca, botões principais) e verde `#0e9a3e` (ações, sucesso).
- Todas as cores estão definidas no topo de `static/css/style.css` (variáveis `--tu-*`).

## Quem recebe os problemas

| Perfil | O que faz |
|---|---|
| Munícipe (`citizen`) | comunica, confirma «Eu também vi», segue freguesias e recebe **alertas** dessas zonas |
| Junta de Freguesia (`staff`) | trata as ocorrências da sua freguesia |
| Serviço técnico (`staff`) | trata as ocorrências da sua área (água, iluminação, obras…) em todo o concelho |
| Câmara Municipal (`admin`) | vê tudo, define prioridades e reencaminha entre entidades |

Encaminhamento automático: serviço da categoria → Junta da freguesia (calculada pelas coordenadas) → Câmara.
Todos recebem alertas: o autor e quem confirmou (mudança de estado), quem segue a freguesia (nova
ocorrência) e a entidade responsável (nova atribuição). Página `/alertas`.

## Imagens de fundo das freguesias

Colocar em `static/img/freguesias/<código>.jpg` (código DICOFRE, lista em `LEIA-ME.txt`; 1600×900, até 500 KB).
Legendas e créditos em `legendas.json`, por exemplo:

```json
{ "050406": { "ponto": "Chafariz de D. João V", "credito": "Foto: autor, licença" } }
```

A imagem aparece no fundo do topo da página inicial ao passar o rato pela freguesia e na página `/freguesias/<código>`.
Usar só fotografias próprias ou com licença que o permita.

## Pôr online (Render + Neon, gratuito)

1. **Base de dados — [Neon](https://neon.com)**: criar conta e projeto (região Frankfurt), copiar o
   *connection string* (`postgresql://…?sslmode=require`).
2. **Código — GitHub**: enviar esta pasta para `github.com/geraldosense/Townly_Up` (ver comandos abaixo).
3. **Servidor — [Render](https://render.com)**: *New → Blueprint* → escolher o repositório. O `render.yaml`
   cria o serviço; colar o endereço do Neon em `DATABASE_URL`. O `start.sh` cria as tabelas, a conta da Câmara e arranca o `gunicorn`.

```bash
git init
git add .
git commit -m "Townly Up"
git branch -M main
git remote add origin https://github.com/geraldosense/Townly_Up.git
git push -u origin main
```

**Todo o conteúdo vem dos utilizadores** (não há dados fictícios). No Render,
definir `ADMIN_EMAIL` e `ADMIN_PASSWORD` — o `start.sh` cria essa 1.ª conta da Câmara. Depois, no site,
*menu → Contas das entidades* cria as contas dos técnicos das Juntas e dos serviços; os munícipes
registam-se sozinhos. Cada pessoa muda a palavra-passe em *A minha conta*.

Notas: no plano gratuito o site adormece após 15 min sem visitas (a 1.ª visita demora ~1 min).
As fotografias ficam guardadas na base de dados (o disco do Render apaga-se ao reiniciar).

## Sistema inteligente

**Repetidos** (`similarity.py`): antes de enviar, a nova ocorrência é comparada com as ativas a menos de
150 m, juntando distância, categoria, palavras da descrição (sem acentos nem palavras vazias) e a
«impressão digital» das fotografias (dHash de 64 bits, guardado em `incident_images.phash`).
Acima de 50 % de semelhança o munícipe vê as ocorrências parecidas e escolhe «Eu também vi» ou
«É outro problema» (fica registado em `incidents.similar_to`). Os técnicos veem a % de semelhança no painel.

**Moderação das fotografias** (`moderation.py`): cada fotografia é analisada pela Claude API (visão,
modelo `claude-haiku-5-5`) e é recusada automaticamente se tiver conteúdo sexual, violência ou ódio,
ou se não mostrar um problema no espaço público (selfie, meme, captura de ecrã...). Cada recusa fica em
`moderation_events`; à 3.ª recusa por conteúdo impróprio em 30 dias a conta é suspensa.
Configurar `ANTHROPIC_API_KEY` (consola da Anthropic → API keys). Sem a chave a moderação fica desligada.

## Email com o número de referência

Logo após comunicar, o munícipe recebe um email com o número (TU-AAAA-NNNN), o resumo, a entidade
responsável, a ligação para acompanhar e os próximos passos (`mailer.py`, `templates/emails/`).
O envio corre em segundo plano: a página não espera e, se falhar, a ocorrência fica registada na mesma.

| Onde | Configuração |
|---|---|
| Online (Render) | `BREVO_API_KEY` + `MAIL_FROM` (remetente verificado na Brevo, gratuito até 300 emails/dia). O Render gratuito bloqueia SMTP, por isso usa-se a API HTTPS. |
| No Mac (opcional) | `MAIL_SERVER=smtp.gmail.com`, `MAIL_PORT=587`, `MAIL_USERNAME`, `MAIL_PASSWORD` (palavra-passe de aplicação do Gmail), `MAIL_FROM` |
| Sem configuração | os emails ficam em `instance/outbox/` (abrir no navegador para ver) |

## Conta, apagar e alertas

- **Fotografia de perfil** (`/conta`): JPEG/PNG/WebP até 5 MB, cortada em quadrado 320×320, sem metadados,
  guardada na base de dados (`users.avatar`). Aparece no menu, na conta e no ranking.
- **Apagar**: o autor pode apagar a ocorrência enquanto está «Registada» ou «Em análise». Deixa de aparecer em
  todo o lado, as fotografias são eliminadas e os pontos retirados; o registo e o histórico ficam
  (`incidents.deleted_at`), como exige o caderno de encargos (RF10/RF11).
- **Alertas da comunidade**: cada novo problema gera um alerta para todos os munícipes («na sua freguesia» para
  quem lá vive ou a segue). Em «Alertas» cada pessoa pode escolher só as freguesias que acompanha (`users.alert_scope`).
