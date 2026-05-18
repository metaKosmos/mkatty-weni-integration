# mkatty-weni-integration

Integração da API de Virtual Try-On da **mKatty** (metaKosmos) com o chatbot **Weni by VTEX** (Agent Builder).

Funciona assim: o usuário manda uma foto + um ou mais códigos SKU de produtos Natura pelo WhatsApp, e o bot devolve as prévias da maquiagem aplicada — usando o catálogo Pulpo, tenant `vto-natura`.

## Como funciona

```
Usuário (WhatsApp)
   ↓ manda foto + SKUs
Weni → Kosmos (Manager IA)
   ↓ roteia
Provador Virtual (Assigned Agent)
   ↓ chama
apply_makeup (Tool Python)
   ↓ POST /pulpo/vto (paralelo, 1 por SKU)
API mKatty
   ↓ retorna imageUrl
   ↓
WhatsAppCarousel via Broadcast
   ↓
Usuário recebe carrossel com as prévias
```

## Stack

| Camada | Tecnologia |
|---|---|
| Definição do agente | YAML (`agent_definition.yaml`) |
| Tool | Python 3.12 + `weni-agents-toolkit` |
| HTTP | `requests` |
| Paralelismo | `concurrent.futures.ThreadPoolExecutor` |
| Deploy | Weni-CLI |
| API externa | mKatty (`/pulpo/vto`, Node/Express) |

## Estrutura

```
.
├── agent_definition.yaml       # manifesto do agente (lido pelo weni-cli)
├── tools/
│   └── apply_makeup/
│       ├── main.py             # a tool em si
│       └── requirements.txt    # dependências da tool
└── README.md
```

## Pré-requisitos

- Python 3.12+
- `pip install weni-cli`
- Conta na Weni com projeto criado e Manager IA configurado (no nosso caso: `Kosmos`)
- API mKatty rodando e acessível em `https://mkatty.metakosmoslab.com/pulpo/vto`

## Deploy

```bash
weni login
weni project list
weni project use <uuid-do-projeto>
weni project push agent_definition.yaml
```

Depois, no Weni Manager, atribua o agente "Provador Virtual de Maquiagem" ao Manager (Kosmos).

## Como testar

No WhatsApp Demo (ou no Preview do Manager):

1. Manda uma foto do rosto + texto tipo: `testa com NATBRA-90905, NATBRA-90906, NATBRA-90907`
2. O bot responde "Aplicando a maquiagem em 3 produtos..."
3. Em 20-40s, chega um carrossel deslizável com as 3 prévias

### Comportamentos cobertos

- **Foto + 1 SKU** → mensagem simples com 1 imagem
- **Foto + 2+ SKUs** → carrossel WhatsApp com até 10 cards
- **Só foto, sem SKU** → bot pede o código
- **Só nome do produto** (ex.: "Batom Matte Faces") → bot pede o SKU
- **SKU inválido** → bot avisa qual falhou
- **Sucesso parcial** (3 SKUs, 2 deram certo) → manda as 2 imagens + texto resumo da falha

## Contrato da API mKatty

```
POST https://mkatty.metakosmoslab.com/pulpo/vto
Content-Type: application/json

Body:
{
  "photoBase64": "<base64 da foto>",
  "productCode": "NATBRA-90905"
}

Response 200:
{
  "success": true,
  "productCode": "NATBRA-90905",
  "product": "Batom Matte Faces",
  "category": "Batom",
  "color": "#A45A57",
  "mimeType": "image/png",
  "imageUrl": "https://storage.mk3dlabs.com/media/pulpo/.../resultado.png"
}
```

## Decisões de design (e por quê)

- **`Broadcast.send()` em vez de `AttachmentResponse`**: o LLM tendia a "reformatar" a saída e perder o anexo. Broadcast bypassa o LLM e manda a mensagem direto pra Flows API, garantindo que a imagem chegue inteira.
- **`FinalResponse()` no final**: encerra a execução sem deixar o LLM gerar uma resposta extra (evita duplicação).
- **Foto baixada uma vez só**: independente de quantos SKUs, a foto é a mesma. Baixa, converte pra base64, reusa.
- **Threads, não async**: `requests` é síncrono, e adicionar `aiohttp` só pra paralelizar 3-10 calls não compensa.
- **Carrossel só com 2+ sucessos**: 1 imagem só fica feio em carrossel; vira mensagem simples.

## Limitações conhecidas

- **Estabilidade depende do LLM**: o Manager IA (Kosmos) pode hesitar, parafrasear ou perguntar coisas desnecessárias. Pra cenários 100% scriptados, a recomendação é usar **Flow Builder** (automação RapidPro) em vez de Agent Builder.
- **Latência**: cada chamada na mKatty leva 10-30s. Em batches grandes pode aproximar do timeout do Lambda da Weni.
- **WhatsApp não tem "1 mensagem com várias imagens" sem carrossel**: por isso usamos `WhatsAppCarousel`.

## Roadmap

- [ ] Reconstruir o fluxo principal em **Flow Builder** (automação determinística, sem LLM no caminho crítico)
- [ ] Endpoint `/pulpo/vto/batch` no backend mKatty pra reduzir N chamadas a 1
- [ ] Endpoint aceitar `photoUrl` direto (sem precisar baixar+base64 do lado do cliente)
- [ ] Tratar callbacks dos botões "Gostei desse" do carrossel
