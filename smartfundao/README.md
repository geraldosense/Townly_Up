<p align="center">
  <img src="static/img/logo.svg" alt="Townly Up" width="132">
</p>

<h1 align="center">Townly Up</h1>

<p align="center">
  <b>Não basta ver. É preciso comunicar.</b><br>
  Plataforma web para comunicar e acompanhar problemas no espaço público, do telemóvel até à resolução.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-0b2a5b">
  <img alt="Flask" src="https://img.shields.io/badge/Flask-3-0b2a5b">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-16-0b2a5b">
  <img alt="Testes" src="https://img.shields.io/badge/testes-146%20a%20passar-0e9a3e">
  <img alt="PWA" src="https://img.shields.io/badge/PWA-instal%C3%A1vel-0e9a3e">
</p>

> **Prova de Aptidão Profissional (PAP)** · 12.º ano · Curso Profissional de Técnico de Desenvolvimento de Software
> Tema: *Desenvolvimento de um sistema digital de identificação de problemas urbanos.*
> Projeto académico, pensado a partir do concelho do Fundão. Não é um serviço oficial de nenhuma Câmara Municipal.

---

## Índice

1. [Sobre o projeto](#sobre-o-projeto)
2. [O protótipo em imagens](#o-protótipo-em-imagens)
3. [Tipos de conta](#tipos-de-conta)
4. [Funcionalidades](#funcionalidades)
5. [Ciclo de vida de uma ocorrência](#ciclo-de-vida-de-uma-ocorrência)
6. [Tecnologias](#tecnologias)
7. [Arquitetura e estrutura](#arquitetura-e-estrutura)
8. [Instalação e execução](#instalação-e-execução)
9. [Primeiro arranque e contas](#primeiro-arranque-e-contas)
10. [Segurança e privacidade](#segurança-e-privacidade)
11. [Sistema inteligente](#sistema-inteligente)
12. [Dados geográficos](#dados-geográficos)
13. [API JSON](#api-json)
14. [Testes](#testes)
15. [Pôr online](#pôr-online)
16. [Variáveis de ambiente](#variáveis-de-ambiente)
17. [Créditos e licenças](#créditos-e-licenças)

---

## Sobre o projeto

Um buraco na estrada, um candeeiro apagado, um contentor a transbordar: toda a gente vê, pouca gente comunica,
e quem comunica raramente sabe o que aconteceu depois. O **Townly Up** resolve isso.

- **O munícipe** fotografa o problema, confirma o local no mapa e recebe um número de referência (`TU-2026-0042`).
- **O pedido segue sozinho** para quem o pode resolver: o serviço técnico da Câmara ou a Junta de Freguesia.
- **Cada mudança de estado** fica registada com data, autor e observação, e o munícipe é avisado.
- **O mapa público** mostra, com cores, o que foi comunicado, o que está em execução e o que já ficou resolvido.

A aplicação foi desenhada **primeiro para o telemóvel** (navegação inferior, menu lateral, mapa em ecrã inteiro) e
instala-se no ecrã inicial como uma app (PWA). Funciona em todo o território continental: cada concelho fica
ativo quando a respetiva Câmara Municipal entra na plataforma. O Fundão é o concelho de referência,
com as 23 freguesias e as suas Juntas.

---

## O protótipo em imagens

Capturas reais da aplicação num telemóvel (390 × 844). As ocorrências mostradas são exemplos.

### Comunicar um problema

<p align="center">
  <img src="docs/imagens/prototipo-municipe.jpg" alt="Início, comunicar, fotografia e número de referência" width="100%">
</p>

### Acompanhar e participar

<p align="center">
  <img src="docs/imagens/prototipo-acompanhar.jpg" alt="Mapa, estado da ocorrência, menu e perfil" width="100%">
</p>

### Área da Câmara e da plataforma

<p align="center">
  <img src="docs/imagens/prototipo-camara.jpg" alt="Câmaras Municipais, todas as ocorrências e estatísticas" width="100%">
</p>

---

## Tipos de conta

| Conta | Quem é | O que pode fazer |
|---|---|---|
| **Munícipe** (`citizen`) | Qualquer pessoa, regista-se sozinha | Comunicar ocorrências, acompanhar as suas, confirmar «Eu também vi», receber alertas, ganhar pontos |
| **Câmara / entidade** (`staff`) | Câmara Municipal de um concelho, Junta de Freguesia ou serviço técnico | Ver e tratar as ocorrências **da sua área**, mudar estados, escrever observações, anexar a fotografia da resolução |
| **Conta principal da plataforma** (`admin`) | Equipa Townly Up | Criar as contas das Câmaras (Distrito → Concelho), aprovar pedidos de acesso, supervisionar todas as ocorrências, ver estatísticas, gerir a segurança da comunidade |

- O registo público cria **sempre** uma conta de munícipe. Nenhuma conta de Câmara ou de administração se cria pelo site público.
- Uma Câmara pode pedir acesso em **Entrar → Câmara → Pedir acesso**; o pedido aparece à conta principal com um botão «Criar conta» que preenche o formulário.
- **Uma Câmara por concelho.** Criar a conta da Câmara ativa logo o concelho; desativá-la volta a pôr o concelho como «ainda não disponível».
- A Câmara de um concelho só vê as ocorrências desse concelho (por exemplo, a Câmara da Covilhã não vê as do Fundão).

---

## Funcionalidades

### Para os munícipes

| Funcionalidade | Descrição |
|---|---|
| **Comunicar em 3 passos** | Categoria e caso concreto → fotografias (até 3) e descrição → local por GPS, com marcador arrastável |
| **Número de referência** | `TU-AAAA-NNNN`, mostrado no ecrã e enviado por email com o resumo e a ligação para acompanhar |
| **Validação do local** | A ocorrência tem de ficar dentro do concelho da pessoa (limites oficiais) |
| **Aviso de repetidos** | Antes de enviar, mostra problemas parecidos já comunicados ali perto |
| **Mapa vivo** | Todas as ocorrências do concelho por cor de estado, camada de freguesias, satélite ou ruas, localização atual e lista «Todas as ocorrências» |
| **«Eu também vi» / «Já foi resolvido?»** | Cada pessoa confirma uma vez; o pedido ganha força sem ficar duplicado |
| **Alertas** | Mudanças de estado das suas ocorrências e novos problemas na zona que acompanha |
| **Pontos, níveis e ranking** | Olho novo → Olho atento → Vigilante do bairro → Guardião da cidade → Embaixador Townly |
| **Perfil** | Fotografia (qualquer formato de imagem, incluindo HEIC), nome, email, palavra-passe, atividade e terminar sessão num só sítio |
| **Definições** | O meu concelho (Distrito → Concelho), notificações, tema claro/escuro, idioma, apagar a conta |
| **Apagar uma ocorrência** | Enquanto estiver «Registada» ou «Em análise»; o histórico fica guardado |
| **Como funciona** | Guia passo a passo com imagens reais da aplicação |

Pontos: comunicar **+10** · «Eu também vi» **+2** · «Já foi resolvido?» **+1** · a sua ocorrência foi confirmada **+1** · a sua ocorrência foi resolvida **+15**.

### Para as Câmaras e entidades

| Funcionalidade | Descrição |
|---|---|
| **Ocorrências da Câmara** | Separadores por estado («Por resolver», «Registada», …), pesquisa por número ou título, filtros por categoria, freguesia e ordem |
| **Tratar um pedido** | Mudar o estado com observação, definir prioridade, reencaminhar para outra entidade, marcar como duplicada (com o ticket original) |
| **Fotografia de resolução** | Mostra ao munícipe o trabalho concluído |
| **Encaminhamento automático** | Serviço da categoria → Junta da freguesia (calculada pelas coordenadas) → Câmara |
| **Avisos por SMS** | As Juntas podem receber por SMS cada nova ocorrência que lhes é atribuída |

### Para a conta principal da plataforma

| Área | Descrição |
|---|---|
| **Câmaras Municipais** | Lista das Câmaras na plataforma, «Adicionar Câmara» (Distrito → Concelho, responsável, email, palavra-passe), ativar e desativar contas |
| **Pedidos de acesso** | Pedidos enviados pelas Câmaras, com contador no menu e na página inicial |
| **Todas as ocorrências** | Supervisão de todos os concelhos ativos |
| **Estatísticas** | Em curso, resolvidas, tempo médio até à 1.ª resposta e até resolver, satisfação, desempenho por entidade, tipos de problema, freguesias, pedidos atrasados e atividade recente |
| **Segurança da comunidade** | Avisos formais, contas banidas (com «Levantar») e fotografias recusadas |

---

## Ciclo de vida de uma ocorrência

```mermaid
flowchart LR
    R[Registada] --> A[Em análise] --> T[Em tratamento] --> S[Resolvida]
    R --> X[Rejeitada<br/>com motivo]
    A --> X
    R --> D[Duplicada<br/>com ticket de referência]
    A --> D
```

- O histórico de estados é **imutável**: triggers na base de dados impedem alterar ou apagar registos antigos.
- As ocorrências fechadas passam para o **arquivo**, nunca são apagadas.
- No mapa: vermelho = recebida, amarelo = em execução, verde = concluída.

---

## Tecnologias

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.11+ · Flask 3 (app factory, blueprints) · Jinja2 |
| Base de dados | PostgreSQL (psycopg 3, pool de ligações) · SQLite como alternativa sem instalação · 16 migrações versionadas |
| Imagens | Pillow (validação pelo conteúdo, rotação EXIF, redução, remoção de metadados GPS) · pillow-heif opcional |
| Frontend | HTML · CSS próprio · Bootstrap 5 · Leaflet 1.9 com mapas Esri · Source Sans 3 / Source Serif 4 |
| Mobile | PWA (manifest + service worker, página offline), HTTPS local para GPS no telemóvel |
| Inteligência | Claude API (visão) para moderação de fotografias · dHash para comparar fotografias |
| Comunicações | Brevo (email e SMS) · SMTP · pasta `outbox` sem configuração |
| Servidor | gunicorn · Render · Neon (PostgreSQL gerido) |
| Testes | pytest · os mesmos testes em SQLite e PostgreSQL |

---

## Arquitetura e estrutura

```
smartfundao/
├── app.py              rotas, erros, comandos CLI (create_app)
├── api.py              API JSON v1
├── services.py         regras de negócio: validação, tickets, estados, encaminhamento, Câmaras
├── security.py         hash de palavras-passe, sessão, perfis, CSRF
├── community.py        confirmações, pontos, níveis, ranking, alertas
├── supervision.py      estatísticas da plataforma
├── similarity.py       deteção de ocorrências repetidas
├── moderation.py       moderação de fotografias (Claude API)
├── profanity.py        filtro de linguagem imprópria
├── territory.py        distritos, concelhos e concelhos ativos
├── geo.py              em que freguesia fica um ponto
├── mailer.py · sms.py  email e SMS
├── db.py               ligação PostgreSQL/SQLite, transações, migrações
├── reference.py        dados de referência: freguesias, entidades, categorias
├── migrations/         postgres/ e sqlite/ (0001 → 0016)
├── templates/          páginas Jinja2 (citizen/, admin/, pages/, emails/, errors/)
├── static/             css/, js/ (live.js, map.js, new_incident.js), img/, data/ (GeoJSON)
├── tests/              146 testes automáticos
├── docs/imagens/       imagens deste README
└── *.command           atalhos para o macOS (duplo clique)
```

**Princípios seguidos**

- Toda a regra de negócio vive em `services.py`; as rotas só validam o pedido e mostram a resposta.
- Restrições também na base de dados (`CHECK`, chaves estrangeiras, índices únicos, triggers), não só no código.
- O mesmo código corre em SQLite e PostgreSQL.
- Fotografias fora de `static/`: só quem tem permissão as vê.

---

## Instalação e execução

### No Mac (mais simples)

| Atalho | O que faz |
|---|---|
| `iniciar.command` | Instala na 1.ª vez, aplica as migrações e abre `http://localhost:8000` |
| `telemovel.command` | Arranca em HTTPS (porta 8443) para testar no telemóvel com GPS e instalar a app |
| `repor_conta_principal.command` | Repõe a conta principal da plataforma |
| `apagar_contas_camara.command` | Apaga as contas das Câmaras (para as criar de novo) |
| `apagar_tudo.command` | Recomeça a base de dados do zero |

> No macOS a porta 5000 está ocupada pelo AirPlay Receiver, por isso usamos a 8000.

### À mão

```bash
cd ~/PAP_2026/smartfundao
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

flask --app app init-db                      # cria as tabelas e os dados de referência
flask --app app create-admin                 # cria a conta principal (pede nome, email e palavra-passe)
flask --app app run --debug --port 8000      # abrir http://127.0.0.1:8000
```

### PostgreSQL

1. Instalar a [Postgres.app](https://postgresapp.com) e carregar em **Initialize**.
2. Correr `iniciar.command`: cria a base `townlyup` e aplica as migrações.
3. Para escolher à mão: `export DATABASE_URL=postgresql://$USER@localhost:5432/townlyup`.

Sem PostgreSQL a aplicação usa SQLite (`instance/townlyup.db`), com o mesmo código.

### Outros comandos

| Comando | Descrição |
|---|---|
| `flask --app app freguesias` | Descarrega os limites oficiais das freguesias (CAOP) |
| `flask --app app repor-admin` | Repõe a palavra-passe da conta principal |
| `flask --app app apagar-contas-camara` | Apaga as contas das Câmaras |
| `flask --app app contas-teste [--remover]` | Cria ou desativa contas só para testes (`@townlyup.test`) |
| `flask --app app apagar-tudo` | Apaga todos os dados |

---

## Primeiro arranque e contas

A base de dados começa **vazia**: só freguesias, entidades e categorias. Todo o conteúdo vem dos utilizadores.

1. **Conta principal**: `flask --app app create-admin` no Mac, ou `ADMIN_EMAIL` + `ADMIN_PASSWORD` online.
2. **Câmaras**: a conta principal abre *Menu → Câmaras Municipais → Adicionar Câmara* e escolhe Distrito → Concelho.
   O concelho fica ativo nesse momento.
3. **Juntas e serviços do Fundão**: *Câmaras Municipais → Mais opções*.
4. **Munícipes**: registam-se sozinhos, escolhendo o seu concelho.

Cada pessoa muda a palavra-passe no **Perfil**.

---

## Segurança e privacidade

- Palavras-passe com hash (Werkzeug); mensagem de login neutra.
- Token CSRF em todos os formulários e pedidos `POST` da API.
- Perfis e permissões verificados em cada rota (`role_required`) e por concelho e entidade.
- Cada munícipe só vê os seus pedidos privados e as suas fotografias (403 para os de outros).
- Uploads: tamanho máximo, tipo verificado pelo **conteúdo**, nome aleatório, metadados EXIF e GPS removidos,
  proteção contra «bombas» de descompressão.
- SQL sempre parametrizado; páginas de erro sem detalhes internos.
- Mapa público só com dados seguros: categoria, estado e número de confirmações, sem nomes nem fotografias.
- **Comunidade**: palavrões, insultos e imagens impróprias são bloqueados. 1.º ato = aviso formal; 2.º ato = conta banida.
- Cada pessoa pode apagar a sua conta nas Definições.

---

## Sistema inteligente

**Deteção de repetidos** (`similarity.py`): a nova ocorrência é comparada com as ativas a menos de 150 m,
juntando distância, categoria, palavras da descrição (sem acentos nem palavras vazias) e a «impressão digital»
das fotografias (dHash de 64 bits). Acima de 50 % de semelhança, o munícipe vê as ocorrências parecidas e
escolhe «Eu também vi» ou «É outro problema». Os técnicos veem a percentagem no painel.

**Moderação das fotografias** (`moderation.py`): cada fotografia é analisada pela Claude API (visão) e é recusada se
tiver conteúdo sexual, violência ou ódio, ou se não mostrar um problema no espaço público (selfie, meme,
captura de ecrã). Sem `ANTHROPIC_API_KEY`, a moderação fica desligada e o resto funciona.

**Filtro de linguagem** (`profanity.py`): títulos, descrições e observações com linguagem imprópria são bloqueados.

---

## Dados geográficos

| Dado | Fonte | Ficheiro |
|---|---|---|
| Freguesias e concelho do Fundão | CAOP, Direção-Geral do Território | `static/data/freguesias_fundao.geojson`, `concelho_fundao.geojson` |
| Distritos e concelhos de Portugal (308) | GEO API PT / CAOP | `static/data/portugal.json` e API |
| Contorno de Portugal continental e distritos | Natural Earth (domínio público) | `portugal_continental.geojson`, `distritos_continente.geojson` |
| Mapas de fundo | Esri World Street Map / World Imagery | carregados pelo Leaflet |

O mapa abre já enquadrado no concelho da pessoa e não deixa sair dos seus limites. Quem não tem sessão iniciada
vê Portugal continental com os distritos.

---

## API JSON

Base para as futuras apps Android e iOS. Prefixo: `/api/v1`.

| Método | Rota | Descrição |
|---|---|---|
| GET | `/categories` | Categorias com ícone e cor |
| GET | `/map` | Ocorrências do mapa público |
| GET | `/incidents/nearby?lat=&lng=&category_id=` | Ocorrências ativas a menos de 50 m |
| POST | `/incidents/similar` | Ocorrências parecidas antes de enviar |
| POST | `/incidents/<id>/confirm` | `{"kind": "seen" \| "fixed"}` (sessão + cabeçalho `X-CSRF-Token`) |
| GET | `/me` | Pontos e nível do utilizador |
| GET | `/parishes` | Freguesias do Fundão |
| GET | `/territorio?concelho=` | Se o concelho já está ativo na plataforma |

---

## Testes

```bash
python -m pytest -q                                                   # SQLite
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/postgres python -m pytest -q   # PostgreSQL
```

**146 testes automáticos**, todos a passar nas duas bases de dados. Cobrem os testes de aceitação do caderno
de encargos, contas e permissões, Câmaras por concelho, encaminhamento, estados e histórico, comunidade e pontos,
repetidos, moderação, perfil, definições, território, SMS e restrições da base de dados.

A experiência no telemóvel deve também ser confirmada à mão (`telemovel.command`) ou nas ferramentas de programador do navegador.

---

## Pôr online

**Render + Neon (gratuito)**

1. **[Neon](https://neon.com)**: criar o projeto (região Frankfurt) e copiar o *connection string*.
2. **GitHub**: enviar o código para `github.com/geraldosense/Townly_Up`.
3. **[Render](https://render.com)**: *New → Blueprint* → escolher o repositório. O `render.yaml` cria o serviço;
   colar o endereço do Neon em `DATABASE_URL` e definir `ADMIN_EMAIL` e `ADMIN_PASSWORD`. O `start.sh` aplica as
   migrações, cria a conta principal e arranca o `gunicorn`.

No plano gratuito o site adormece após 15 minutos sem visitas. As fotografias ficam na base de dados,
porque o disco do Render apaga-se ao reiniciar.

**Escala** (PostgreSQL 16, 10 000 munícipes, 2 000 ocorrências): criar uma ocorrência ≈ 9 ms · mapa ≈ 130 ms ·
painel ≈ 100 ms · alertas ≈ 4 ms.

---

## Variáveis de ambiente

Nenhuma é obrigatória para correr no Mac. As chaves ficam **sempre** fora do código (ficheiro `.env` ou painel do Render).

| Variável | Para quê |
|---|---|
| `SECRET_KEY` | Assinar as sessões (obrigatória em produção) |
| `DATABASE_URL`, `DB_POOL_SIZE` | Base de dados PostgreSQL |
| `ADMIN_EMAIL`, `ADMIN_NAME`, `ADMIN_PASSWORD` | Conta principal criada no arranque |
| `SESSION_COOKIE_SECURE`, `TRUST_PROXY`, `PUBLIC_URL` | HTTPS e endereço público |
| `ANTHROPIC_API_KEY`, `MODERATION_MODEL` | Moderação das fotografias |
| `BREVO_API_KEY`, `SMS_SENDER` | Email e SMS pela Brevo |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`, `MAIL_FROM_NAME` | Email por SMTP |
| `CONTACT_EMAIL` | Contacto mostrado nas páginas de ajuda |
| `TEST_ACCOUNTS` | Permitir as contas de teste |
| `PLAY_STORE_URL`, `APP_STORE_URL` | Ligações para as futuras apps |
| `TEMPLATES_AUTO_RELOAD` | Recarregar as páginas durante o desenvolvimento |

---

## Identidade visual

- **Logótipo**: `static/img/logo.svg` (olho + marcador + anel), também usado como ícone da app.
- **Cores**: azul-marinho `#0b2a5b` (marca) e verde `#0e9a3e` (ações e sucesso), definidas no topo de `static/css/style.css` (`--tu-*`).
- **Tipografia**: Source Sans 3 (texto) e Source Serif 4 (títulos).
- Tema claro por omissão, com tema escuro nas Definições.

---

## Créditos e licenças

- Limites administrativos: **CAOP**, Direção-Geral do Território · **GEO API PT**.
- Contornos de Portugal e distritos: **Natural Earth** (domínio público).
- Mapas de fundo: **Esri**, Maxar, Earthstar Geographics e comunidade de utilizadores SIG.
- Bibliotecas: Flask, Pillow, psycopg, Leaflet, Bootstrap e Bootstrap Icons, segundo as respetivas licenças.

<p align="center"><sub>Townly Up · PAP 2026/27 · Geraldo Sense</sub></p>
