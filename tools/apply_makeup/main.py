"""
Tool do agente "Provador Virtual de Maquiagem".

Fluxo geral:
1. Recebe do LLM uma photo_url e uma lista de SKUs (separados por vírgula).
2. Baixa a foto UMA VEZ e converte pra base64.
3. Chama a API mKatty (/pulpo/vto) em paralelo, uma vez por SKU.
4. Devolve as prévias direto no WhatsApp:
   - 2+ sucessos → carrossel WhatsApp (1 mensagem só, deslizável)
   - 1 sucesso → mensagem simples com texto + imagem
   - falhas → texto explicando o que deu errado

A entrega das imagens é feita via Broadcast.send(), que bypassa o LLM e
manda direto pra API da Weni. Isso garante que o attachment chegue intacto
no WhatsApp (se a gente deixasse o LLM "compor" a resposta ele tirava o
attachment e mandava só texto com a URL).
"""

import base64
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import requests

from weni import Tool
from weni.broadcasts import Broadcast
from weni.broadcasts.messages import (
    Message,
    WhatsAppCarousel,
    WhatsAppCarouselQuickReply,
    WhatsAppCarouselSlide,
)
from weni.context import Context
from weni.responses import FinalResponse, TextResponse


# Endpoint da API mKatty Virtual Try-On (tenant vto-natura).
# É a mesma URL que você testou no curl — pública, sem auth.
VTO_ENDPOINT = "https://mkatty.metakosmoslab.com/pulpo/vto"

# Timeout pra baixar a foto do WhatsApp/CDN. Curto porque é só uma imagem.
DOWNLOAD_TIMEOUT = 20

# Timeout pra cada chamada na mKatty. Mais alto porque envolve Chromium + try-on.
VTO_TIMEOUT = 90

# Limite de SKUs por chamada. Mais que isso pode estourar o Lambda da Weni
# e sobrecarregar o pool de Chromium do mKatty (que é 2 sessões simultâneas).
MAX_PRODUCTS = 10

# Quantas chamadas paralelas pra mKatty. 5 é seguro mesmo com pool=2 no backend,
# porque a fila do mKatty serializa internamente sem dar timeout.
PARALLEL_WORKERS = 5


@dataclass
class TextWithAttachment(Message):
    """
    Mensagem custom: 1 texto + 1 anexo de imagem.

    O SDK da Weni não tem um Message pronto pra "texto + imagem" no canal
    WhatsApp, então a gente cria essa subclasse pequena. A Flows API aceita
    o campo `attachments` no payload, no formato "mimetype:url".

    Usada quando só 1 SKU dá sucesso (não tem sentido fazer carrossel de 1).
    """

    text: str
    attachment: str

    def format_message(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "attachments": [self.attachment],
        }


class ApplyMakeup(Tool):
    """
    Tool principal. O Weni instancia essa classe e chama .execute(context).

    O `context` traz tudo: parâmetros do LLM, credenciais configuradas na UI,
    dados do contato (WhatsApp), do projeto, etc.
    """

    def execute(self, context: Context):
        # O LLM passa esses parâmetros baseado no que o usuário disse + no schema do YAML.
        raw_photo = context.parameters.get("photo_url", "") or ""
        raw_codes = context.parameters.get("product_code", "") or ""

        # Limpa a URL (LLM às vezes deixa o prefixo [image/jpeg:] passar).
        photo_url = self._extract_url(raw_photo)
        # Quebra a string "NATBRA-90905, NATBRA-90906" em lista.
        product_codes = self._parse_codes(raw_codes)

        # Validações defensivas — se o LLM mandar algo estranho, a gente recupera.
        if not photo_url:
            return TextResponse(
                data="Não consegui ler a foto enviada. Pode mandar a imagem novamente?"
            )
        if not product_codes:
            return TextResponse(
                data="Preciso de pelo menos um código de produto (ex.: NATBRA-90905) pra aplicar a maquiagem."
            )
        if len(product_codes) > MAX_PRODUCTS:
            return TextResponse(
                data=f"Posso testar até {MAX_PRODUCTS} produtos por vez. Você mandou {len(product_codes)}."
            )

        # Baixa a foto UMA VEZ só. Se a gente baixasse dentro de cada thread,
        # o tempo total ia explodir pra nada (a foto é a mesma).
        try:
            photo_b64 = self._download_as_base64(photo_url)
        except requests.RequestException as exc:
            return TextResponse(
                data=f"Não consegui baixar a foto ({exc}). Tenta reenviar a imagem."
            )

        # Feedback pro usuário enquanto a gente processa (caso seja >1 SKU).
        # Sem isso, ele fica olhando "digitando..." por 20-30s achando que travou.
        if len(product_codes) > 1:
            Broadcast(self).send(
                _PlainText(text=f"Aplicando a maquiagem em {len(product_codes)} produtos, aguarda um instante...")
            )

        # Dispara N chamadas paralelas pra mKatty.
        results = self._apply_in_parallel(photo_b64, product_codes)

        # Separa o que deu certo do que deu errado, mantendo a ordem original.
        successes = [
            (code, outcome)
            for code, outcome in zip(product_codes, results)
            if outcome["ok"]
        ]
        failures = [
            f"- {code}: {outcome['error']}"
            for code, outcome in zip(product_codes, results)
            if not outcome["ok"]
        ]

        broadcast = Broadcast(self)

        # Caminho A: 2 ou mais sucessos → carrossel WhatsApp (uma mensagem com vários cards).
        if len(successes) >= 2:
            broadcast.send(
                WhatsAppCarousel(
                    text=f"Pronto! Aqui estão {len(successes)} prévias da maquiagem:",
                    # Cada card precisa ter sua imagem. Mantém a ordem dos SKUs.
                    attachments=[outcome["attachment"] for _, outcome in successes],
                    # Cada slide é um card no carrossel.
                    carousel=[
                        WhatsAppCarouselSlide(
                            body=outcome["text"],
                            # Botão obrigatório pelo WhatsApp Cloud API.
                            # Por enquanto é só decorativo (a gente não trata o callback ainda).
                            buttons=[
                                WhatsAppCarouselQuickReply(
                                    button_id=f"choose_{code}",
                                    title="Gostei desse",
                                )
                            ],
                        )
                        for code, outcome in successes
                    ],
                )
            )
        # Caminho B: 1 sucesso só → mensagem simples (carrossel de 1 card fica feio).
        elif len(successes) == 1:
            _, outcome = successes[0]
            broadcast.send(
                TextWithAttachment(
                    text=f"Prévia pronta com {outcome['text']}. O que achou?",
                    attachment=outcome["attachment"],
                )
            )

        # Reporta falhas em formato amigável.
        # Se TUDO falhou, é o único caminho de saída.
        if failures and not successes:
            return TextResponse(
                data="Nenhum dos produtos pôde ser aplicado:\n" + "\n".join(failures)
            )
        # Sucessos parciais: já mandamos as imagens via broadcast, agora avisa do que falhou.
        if failures:
            return TextResponse(
                data="Alguns produtos não puderam ser aplicados:\n" + "\n".join(failures)
            )
        # Tudo certo. FinalResponse() encerra a execução sem deixar o LLM
        # tentar gerar uma resposta adicional (evita duplicar mensagens).
        return FinalResponse()

    def _apply_in_parallel(
        self, photo_b64: str, codes: list[str]
    ) -> list[dict[str, Any]]:
        """
        Dispara N chamadas pra mKatty em paralelo usando ThreadPool.

        Por que threads e não async: requests é síncrono, e adicionar aiohttp
        só pra isso aumentaria a dependência sem ganho relevante (são poucas calls).
        """
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
            return list(pool.map(lambda c: self._apply_single(photo_b64, c), codes))

    @staticmethod
    def _apply_single(photo_b64: str, product_code: str) -> dict[str, Any]:
        """
        Faz UMA chamada na mKatty e normaliza o resultado.

        Sempre retorna um dict no mesmo formato:
        - {"ok": True, "text": "Nome (cor X)", "attachment": "image/png:URL"}
        - {"ok": False, "error": "motivo legível"}

        Isso simplifica o tratamento lá em cima (não precisa try/except por SKU).
        """
        try:
            response = requests.post(
                VTO_ENDPOINT,
                json={"photoBase64": photo_b64, "productCode": product_code},
                timeout=VTO_TIMEOUT,
            )
            data = response.json()
        except requests.RequestException as exc:
            return {"ok": False, "error": f"erro de rede ({exc})"}
        except ValueError:
            return {"ok": False, "error": "resposta inválida do provador"}

        # A API mKatty retorna success=false quando o slug não existe ou a foto é ruim.
        if not data.get("success"):
            return {"ok": False, "error": data.get("error", "produto não encontrado")}

        # Defesa extra: success=true mas sem imageUrl (não devia acontecer, mas garante).
        image_url = data.get("imageUrl")
        if not image_url:
            return {"ok": False, "error": "URL da imagem não retornada"}

        # Monta o texto e o anexo no formato que a Flows API espera.
        product = data.get("product", product_code)
        color = data.get("color", "")
        mime_type = data.get("mimeType", "image/png")
        color_suffix = f" (cor {color})" if color else ""

        return {
            "ok": True,
            "text": f"{product}{color_suffix}",
            "attachment": f"{mime_type}:{image_url}",
        }

    @staticmethod
    def _extract_url(raw: str) -> str:
        """
        Extrai uma URL http(s) de uma string qualquer.

        Cobre o caso comum do WhatsApp mandar "[image/jpeg:https://...]" e
        o caso normal de URL pura. Se vier nada parecido, retorna string vazia.
        """
        if not raw:
            return ""
        raw = raw.strip()
        match = re.search(r"https?://[^\s\]\)\>]+", raw)
        return match.group(0) if match else ""

    @staticmethod
    def _parse_codes(raw: str) -> list[str]:
        """
        Quebra uma string tipo "NATBRA-90905, NATBRA-90906 NATBRA-90907"
        numa lista limpa, deduplicada e em maiúsculo.

        Aceita vírgula, ponto-vírgula ou espaços como separador — assim
        o LLM pode formatar do jeito que preferir.
        """
        if not raw:
            return []
        parts = re.split(r"[,;\s]+", raw.strip())
        seen: set[str] = set()
        out: list[str] = []
        for part in parts:
            code = part.strip().upper()
            if code and code not in seen:
                seen.add(code)
                out.append(code)
        return out

    @staticmethod
    def _download_as_base64(url: str) -> str:
        """
        Baixa uma imagem e retorna o conteúdo em base64.

        A API mKatty espera base64 (não URL), então essa conversão é
        obrigatória. Se no futuro o backend aceitar photoUrl direto,
        essa função some.
        """
        resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT)
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode("ascii")


@dataclass
class _PlainText(Message):
    """
    Mensagem custom de texto puro pra usar via Broadcast.

    A gente usa só pra avisar "Aplicando em N produtos..." antes do processamento
    real, dando feedback pro usuário não achar que travou.
    """

    text: str

    def format_message(self) -> dict[str, Any]:
        return {"text": self.text}
