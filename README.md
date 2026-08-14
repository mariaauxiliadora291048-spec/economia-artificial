# Economia Artificial

**Economia Artificial** é a fundação de um laboratório de agentes de IA que
operam como unidades econômicas persistentes. Eles recebem objetivos,
identidade, capital, memória, informação e capacidades; a estratégia não é
pré-programada.

O primeiro ciclo autônomo é:

```text
percepção → deliberação por LLM → pesquisa web → ação governada
→ observação → memória → nova iniciativa
```

## Entrega atual

- `OpenAIResponsesDecisionProvider`: um provedor cognitivo real, baseado na
  Responses API e em chamadas de ferramenta; o modelo escolhe a próxima ação.
- `AutonomousAgentRuntime`: executa decisões sequenciais, devolve cada
  observação ao modelo, registra experiências e cobra computação virtual.
- `WikipediaResearchClient`: pesquisa real, somente leitura, em fonte pública
  allowlisted. Não há acesso de escrita à web.
- identidade, estado financeiro, produtos, memória episódica/estratégica e
  sinais agregados de mercado por agente.
- Action Gateway, Policy Engine, capacidades explícitas, custo econômico,
  ledger append-only e eventos de auditoria.
- schema PostgreSQL para simulações, memória, relações, capacidades e
  aprovações de ações externas.

## Segurança e progressão

O sistema suporta quatro modos: `simulation`, `sandbox`, `paper` e `real`.
Hoje, apenas `web.research` existe como capacidade de mundo real e ainda exige
uma concessão explícita ao agente. Publicação, comunicação com pessoas,
movimentação financeira e credenciais são representadas como capacidades, mas
não possuem conector e são bloqueadas pelo Policy Engine.

Isso não reduz a autonomia cognitiva: o agente decide se deve pesquisar,
criar, precificar ou publicar um produto simulado. A governança só limita o
alcance de efeitos externos.

## Executar os testes

Requer Python 3.12+.

```powershell
cd economia-artificial
uv sync --extra dev
uv run pytest
uv run ruff check .
```

## Demonstrar um ciclo autônomo localmente

O caminho prioritário não exige uma chamada paga: inicie um servidor de modelo
local compatível com a API OpenAI (por exemplo, Ollama ou LM Studio) e, depois,
inicie o control plane. A tela descobre somente endpoints em `localhost` e os
marca como `LOCAL` e `FREE`.

```powershell
cd economia-artificial
uv sync --extra dev
uv run python -m economia_artificial.server
```

Abra [http://127.0.0.1:8787](http://127.0.0.1:8787), clique em `SCAN LOCAL
LLMS`, escolha um modelo encontrado e salve o provider. Em seguida, crie `A01`
com objetivo, capital, modelo, capacidades e budgets; clique em `Start` para
iniciar o scheduler. A chave nunca volta pela API e só fica na memória do
processo; para reinícios, informe somente o nome de uma variável de ambiente.

Um provider de nuvem só é utilizável se o operador o configurar explicitamente.
O provedor usa `store=False`; publicação, e-mail, redes sociais, telefone e
pagamentos não são habilitados pelo servidor.

## Arquitetura

```text
Cognitive Core (LLM)
        ↓
Autonomous Agent Runtime
        ↓
Policy / Risk Engine ── capability grants ── human approval (high risk)
        ↓
Action Gateway
        ↓
Market · Read-only Web Research · future external connectors
        ↓
Ledger · Events · Memory · PostgreSQL
```

## Control plane local

O servidor local inicia sem depender de FastAPI para o milestone: ele usa a
biblioteca padrão do Python, uma API JSON e painel web com atualização a cada
dois segundos. Isso mantém a instalação simples e deixa a fronteira HTTP pronta
para uma futura migração para FastAPI/WebSocket.

```powershell
cd economia-artificial
uv run python -m economia_artificial.server
```

Abra [http://127.0.0.1:8787](http://127.0.0.1:8787). Pelo painel é possível:

- configurar um provider e modelo; a chave informada nunca volta pela API e só
  fica na memória do processo; para reinícios use uma variável de ambiente;
- executar `Scan Local LLMs` para Ollama, LM Studio, vLLM e llama.cpp em
  `localhost`;
- criar, registrar, iniciar, pausar e retomar agentes;
- conceder `web.research` e `agent.create` de modo explícito;
- acompanhar população, ciclos, eventos e patrimônio virtual.

O scheduler usa uma única thread leve, estados persistidos e backoff após
falhas. Ele restaura a população, identidade, memória, ledger, capacidades,
recursos e agenda local após reiniciar o servidor.

## Provider Registry

O registry separa três camadas: catálogo de metadados (`provider_catalog.py`),
adapters reutilizáveis (`provider_adapters.py`) e as configurações locais
(`providers.py`). O painel lista todos os providers conhecidos, mas apresenta
explicitamente a situação de integração: `EXECUTABLE`, `OPENAI_COMPATIBLE`,
`NATIVE_ADAPTER`, `METADATA_ONLY` ou `LOCAL`.

Uma configuração não concede automaticamente a credencial a um agente. O
scheduler registra o provider escolhido como uma concessão explícita por agente
e o Policy Engine bloqueia sua utilização sem essa concessão. O Model Router
só seleciona configurações habilitadas, concedidas e compatíveis com os
requisitos de ferramentas, visão, raciocínio e embeddings.

As credenciais podem vir de variável de ambiente, do formulário local ou do
secret store local protegido por DPAPI no Windows. A configuração persistida
nunca contém a chave em texto puro; a API retorna apenas o estado configurado e
uma máscara parcial quando permitido.

### Demonstração de reprodução econômica

1. Configure um provider com uma chave válida e crie `A01` com `Web Read` e
   `Criar agentes`.
2. Inicie `A01`. O scheduler entrega a percepção atual ao LLM em cada ciclo;
   as ações, resultados e reflexões tornam-se memória.
3. Quando o modelo concluir que uma missão justifica delegação, ele pode chamar
   `agent.create`. A ação exige capacidade, transfere capital e quotas do pai,
   registra o evento e inicia o filho com o mesmo provider.

Conectores de social, e-mail, telefone, computador e pagamentos estão somente
como contratos desabilitados. `draft` e `publish`/`send` são operações distintas
e nenhum conector público ou financeiro é ativado por este servidor.

## Próximas capacidades

1. Adaptador PostgreSQL para substituir o armazenamento de referência em
   memória/JSON.
2. Pesquisa multi-fonte, com reputação e citação de evidências.
3. Relações, negociação e mensagens inicialmente em sandbox.
4. Conectores de escrita externos com aprovação de ação específica, limites de
   gasto, kill switch e contas segregadas.
