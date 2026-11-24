"""A small household library in Spanish and English, for the relevance tests.

Each collection has a few documents on related subjects, so a search must
tell them apart by meaning. The queries share meaning, not keywords, with
the document they must find.
"""

# Path in the library -> passages.
DOCUMENTS: dict[str, list[str]] = {
    "Library/Invoices/2026-03-power.pdf": [
        "Invoice for power supply. Billing period: 1 to 31 March 2026. "
        "Consumption: 312 kWh at 0.14 EUR per kWh. Total amount due: 52.80 EUR.",
    ],
    "Library/Invoices/2026-03-agua.pdf": [
        "Recibo del agua del primer trimestre de 2026. Consumo: 18 metros cúbicos. "
        "Cuota de servicio y canon de saneamiento. Importe total: 41,30 euros.",
    ],
    "Library/Invoices/2026-03-fibra.pdf": [
        "Factura de fibra óptica y línea móvil de marzo de 2026: 600 Mb simétricos "
        "y 50 GB de datos. Importe: 45,00 euros con IVA.",
    ],
    "Library/Manuals/washing-machine.pdf": [
        "Washing machine user guide. Load the drum, choose a programme and press start.",
        "Warranty: the manufacturer repairs or replaces the washing machine for free "
        "during three years from the date of purchase, if you keep the receipt.",
    ],
    "Library/Manuals/caldera.pdf": [
        "Manual de la caldera de gas. Para encender la calefacción, gire el mando "
        "hasta la posición de invierno. Revise la presión del circuito cada mes: "
        "debe estar entre 1 y 1,5 bar.",
    ],
    "Library/Manuals/television.pdf": [
        "Television quick start. Pair the remote control, connect the aerial cable "
        "and tune the channels from the settings menu.",
    ],
    "Library/Contracts/alquiler-piso.pdf": [
        "Contrato de arrendamiento de vivienda. El arrendador cede al arrendatario el "
        "uso del piso de la calle Mayor 12 por una renta mensual de 750 euros, con una "
        "fianza de dos mensualidades y una duración de cinco años.",
    ],
    "Library/Contracts/employment.pdf": [
        "Employment contract. The employee joins the company as a software developer "
        "with a gross annual salary of 32,000 EUR, a trial period of six months and "
        "23 working days of paid holidays.",
    ],
    "Library/Medical/analitica.pdf": [
        "Resultados de la analítica de sangre: hemoglobina 14,2 g/dl, glucosa 92 mg/dl, "
        "colesterol total 185 mg/dl. Todos los valores están dentro del rango normal.",
    ],
    "Library/Medical/dentist.pdf": [
        "Dental clinic appointment: cleaning and check-up on 14 April at 10:30. "
        "Please arrive ten minutes early.",
    ],
    "Library/School/notas.pdf": [
        "Boletín de calificaciones del primer trimestre. Matemáticas: notable. "
        "Lengua castellana: sobresaliente. Inglés: bien. Faltas de asistencia: 2.",
    ],
    "Library/Taxes/renta-2025.pdf": [
        "Declaración del Impuesto sobre la Renta de las Personas Físicas, ejercicio "
        "2025. Rendimientos del trabajo: 31.200 euros. Resultado: a devolver 412 euros.",
    ],
}

# Query -> the document it must find first, among every document of the
# library or only in the document's collection.
QUERIES: dict[str, str] = {
    # The example of the user story: same meaning, no shared words.
    "electricity bill": "Library/Invoices/2026-03-power.pdf",
    # Spanish query, English document.
    "¿Cuándo caduca la garantía de la lavadora?": "Library/Manuals/washing-machine.pdf",
    # English queries, Spanish documents.
    "rental agreement for the flat": "Library/Contracts/alquiler-piso.pdf",
    "blood test results": "Library/Medical/analitica.pdf",
    "school report with my son's grades": "Library/School/notas.pdf",
    "income tax return": "Library/Taxes/renta-2025.pdf",
    "how do I turn on the heating": "Library/Manuals/caldera.pdf",
    # Spanish queries, English documents.
    "cita con el dentista": "Library/Medical/dentist.pdf",
    "mi contrato de trabajo como programador": "Library/Contracts/employment.pdf",
}
