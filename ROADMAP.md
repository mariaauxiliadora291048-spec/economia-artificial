# Documento — Ponto de Melhoria EA-001

**Projeto:** Economia Artificial  
**Categoria:** Arquitetura / Persistência / Observabilidade  
**Status:** Identificado — não bloquear o desenvolvimento atual  

## Título
**Persistência e separação de Memória, Auditoria, Event Stream, Runtime State e Ledger**

---

## 1. Situação atual

O sistema já possui mecanismos distintos para:

- Memória dos agentes;
- Estado do *runtime/scheduler*;
- Estado econômico e *ledger*;
- `ToolCall`;
- `Event`;
- Atividades exibidas no *dashboard*.

Entretanto, `ToolCall` e `Event` atualmente permanecem associados ao estado em memória do `ActionGateway`, enquanto outros componentes já possuem mecanismos de persistência.

## 2. Problema

Após uma reinicialização do processo, informações de auditoria e eventos podem não estar disponíveis para consulta histórica.

Isso prejudica:

- A auditoria;
- O diagnóstico;
- A reconstrução do comportamento dos agentes;
- A observabilidade;
- A análise posterior dos ciclos;
- A evolução para múltiplas LLMs.

## 3. Solução planejada

Criar um *Audit/Event Store* persistente, inicialmente baseado em JSON, sem introduzir PostgreSQL prematuramente.

**Estrutura conceitual:**

```text
Economia Artificial
│
├── MEMORY
│   └── aquilo que o agente aprende/retém
│
├── AUDIT
│   └── aquilo que foi executado/tentado
│
├── EVENT STREAM
│   └── acontecimentos do mundo
│
├── RUNTIME STATE
│   └── estado do scheduler/agente
│
└── LEDGER
    └── verdade financeira
```

## 4. Primeira implementação futura

Algo conceitualmente semelhante a:

```text
.economia-artificial-data/
├── world.json
├── runtime.json
├── agent-memory.json
└── audit-events.json
```

Mantendo interfaces que permitam posteriormente trocar:

`JSON` ➔ `Repository interfaces` ➔ `PostgreSQL`

## 5. Regra arquitetural

**Memória não deve substituir auditoria.**

Uma reflexão do agente, por exemplo:

> “A estratégia X pareceu promissora.”

é **memória**.

Já:

> `product.price` executado no ciclo 17, produto X, preço Y, resultado Z

é **auditoria**.

E:

> `product.sale`

é um **evento econômico**.

Essa distinção será especialmente importante quando o sistema passar a trabalhar com vários *providers*/LLMs simultaneamente.

## 6. Prioridade

**Média/alta**, mas não bloqueante.

# ROADMAP — Economia Artificial

## Objetivo

Evoluir o Economia Artificial de um laboratório econômico simulado
para uma plataforma de agentes autônomos governados, observáveis,
persistentes e capazes de utilizar diferentes modelos de IA.

---

## Melhorias identificadas

### 1. Roteamento inteligente de modelos/providers

**Status:** 🟡 Identificado

O sistema já possui:

- catálogo de providers;
- configuração de múltiplos providers;
- descoberta de modelos;
- suporte a modelos locais;
- `ModelRouter`;
- capacidades como tools, vision, reasoning e embeddings;
- concessão explícita de providers aos agentes.

Porém, o roteamento ainda pode evoluir.

#### Problema atual

O `ModelRouter` seleciona um provider configurado e concedido
ao agente, considerando suas capacidades, mas a arquitetura ainda
não representa completamente uma estratégia dinâmica de seleção
de modelos baseada em:

- custo;
- latência;
- qualidade;
- disponibilidade;
- contexto necessário;
- orçamento do agente;
- tipo de tarefa;
- confiabilidade;
- fallback;
- modelos locais versus cloud.

#### Evolução desejada

Criar um sistema de roteamento capaz de escolher o modelo mais
adequado para cada tarefa, respeitando:

1. capacidades exigidas;
2. providers autorizados;
3. orçamento disponível;
4. custo estimado;
5. disponibilidade;
6. qualidade esperada;
7. prioridade da tarefa;
8. estratégia de fallback.

#### Resultado esperado

O agente não deverá simplesmente possuir "um modelo".

Ele deverá possuir acesso a um **conjunto autorizado de modelos/providers**
e o sistema deverá decidir qual utilizar em cada ciclo ou tarefa.

---

## Próximas melhorias

### 2. Memória persistente dos agentes

**Status:** 🔴 A fazer

Evoluir a memória atualmente utilizada pelo `EconomyWorld` para uma
camada persistente e pesquisável.

Objetivos:

- memória de longo prazo;
- recuperação por relevância;
- memória de estratégia;
- memória de resultados econômicos;
- persistência entre reinicializações;
- possibilidade de integração futura com memória externa.

---

### 3. Observabilidade dos agentes

**Status:** 🟡 Parcial

Expandir a observabilidade para permitir acompanhar:

- decisões;
- ferramentas utilizadas;
- resultados;
- custos;
- ciclos;
- erros;
- evolução patrimonial;
- hipóteses testadas;
- resultados das hipóteses.

Sem expor cadeia de pensamento privada.

---

### 4. Economia e mercado

**Status:** 🟡 Em evolução

Expandir progressivamente:

- clientes;
- demanda;
- concorrência;
- preços;
- produtos;
- custos;
- receitas;
- ativos;
- passivos;
- investimentos;
- falências;
- reputação.

---

### 5. Governança e segurança

**Status:** 🟡 Em evolução

Manter como princípio:

> O agente pode decidir autonomamente dentro das capacidades,
> recursos e permissões que lhe foram concedidos.

Nenhuma capacidade deve ser concedida implicitamente.

---

## Princípios do projeto

- Autonomia com governança.
- Recursos econômicos limitados.
- Capacidades explicitamente concedidas.
- Persistência.
- Observabilidade.
- Reprodutibilidade quando possível.
- Separação entre decisão do agente e execução no mundo.
- Nenhuma cadeia de pensamento privada deve ser armazenada ou exposta.