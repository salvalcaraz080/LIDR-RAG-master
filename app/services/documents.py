"""Extracción de texto de documentos — agnóstico de dominio.

Convierte bytes de un documento a texto plano (bytes → str). Es la semilla del pipeline
de chunking del módulo RAG (sesiones 7-8). No conoce HTTP, LLM, Redis ni el dominio
de la aplicación que lo use.

Uso esperado: quien llame a `extract_text` desde una capa async (p. ej. un servicio
FastAPI) debe ofrecerlo a un thread para no bloquear el event loop con documentos grandes:
    text = await asyncio.to_thread(extract_text, file_bytes, filename)
    # o en FastAPI: await run_in_threadpool(extract_text, file_bytes, filename)
La decisión de cómo lanzarlo queda en el cableado, no en este módulo (es CPU-bound puro).
"""

import time
from io import BytesIO
from pathlib import Path

import structlog
from docx import Document
from pypdf import PdfReader

log = structlog.get_logger()

# Extensiones soportadas — frozenset para lookups O(1) y señal de inmutabilidad
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx"})


class DocumentExtractionError(Exception):
    """Error de dominio del módulo: tipo no soportado, archivo corrupto o sin texto extraíble."""


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Extrae texto plano de un único documento. bytes → str.

    Dispatch por extensión de `filename` (case-insensitive). HTTP-agnóstico: recibe bytes,
    no conoce UploadFile ni ningún objeto HTTP.

    Invariantes:
    - Devuelve siempre un string no-vacío (sin whitespace).
    - Nunca propaga excepciones internas de pypdf/python-docx; las envuelve en
      DocumentExtractionError para aislar el acoplamiento a las librerías.

    Levanta DocumentExtractionError si:
    - La extensión no está en SUPPORTED_EXTENSIONS.
    - Los bytes están corruptos o la librería no puede parsearlos.
    - El texto extraído está vacío o es solo whitespace (p. ej. PDF escaneado sin OCR).
    """
    start = time.monotonic()
    extension = Path(filename).suffix.lower()

    # Rechazar extensiones no soportadas antes de intentar parsear
    if extension not in SUPPORTED_EXTENSIONS:
        log.warning("document_extraction_unsupported", filename=filename, extension=extension)
        raise DocumentExtractionError(
            f"Tipo de archivo no soportado: '{extension}'. "
            f"Extensiones aceptadas: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    # Dispatch a helper específico según extensión
    if extension == ".pdf":
        text = _extract_pdf(file_bytes)
        pages: int | None = text.count("--- Page ")
    else:
        text = _extract_docx(file_bytes)
        pages = None

    # Guarda de vacío: el módulo nunca devuelve texto vacío silenciosamente
    if not text.strip():
        log.warning(
            "document_extraction_empty",
            filename=filename,
            extension=extension,
            hint="El documento puede ser escaneado o no tener capa de texto (pypdf no hace OCR).",
        )
        raise DocumentExtractionError(
            f"No se extrajo texto de '{filename}'. El archivo puede ser un PDF escaneado "
            "sin capa de texto (este módulo no realiza OCR)."
        )

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    log.info(
        "document_extracted",
        filename=filename,
        extension=extension,
        pages=pages,
        chars=len(text),
        duration_ms=duration_ms,
    )
    return text


def _extract_pdf(file_bytes: bytes) -> str:
    """Extrae texto de un PDF página a página.

    Antepone un marcador '--- Page N ---' a cada página (N empieza en 1). Este marcador
    refleja la estructura intrínseca del documento; no es un delimitador de prompt.
    Las páginas se unen con doble salto de línea.

    page.extract_text() puede devolver None (pypdf no garantiza string) → se trata como "".
    """
    try:
        reader = PdfReader(BytesIO(file_bytes))
        parts: list[str] = []
        for i, page in enumerate(reader.pages, start=1):
            # None es posible cuando pypdf no puede extraer texto de la página
            page_text = page.extract_text() or ""
            # Omitir marcador en páginas sin contenido (p. ej. páginas en blanco)
            # para que el texto resultante quede vacío y active la guarda de vacío
            if page_text.strip():
                parts.append(f"--- Page {i} ---\n{page_text}")
        return "\n\n".join(parts)
    except Exception as exc:
        raise DocumentExtractionError(f"No se pudo parsear el PDF: {exc}") from exc


def _extract_docx(file_bytes: bytes) -> str:
    """Extrae texto de un documento Word párrafo a párrafo.

    Los párrafos se unen con saltos de línea simples. No hay marcadores de página:
    python-docx no expone saltos de página de forma fiable (a diferencia de pypdf,
    donde la paginación es estructural), por lo que la asimetría con PDF es intencionada.
    """
    try:
        doc = Document(BytesIO(file_bytes))
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception as exc:
        raise DocumentExtractionError(f"No se pudo parsear el DOCX: {exc}") from exc
