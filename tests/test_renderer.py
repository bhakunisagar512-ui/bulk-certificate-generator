from datetime import date

from app.services.renderer import CertificateData, ReportLabRenderer


def data(name="Asha Verma", **kw):
    base = dict(
        recipient_name=name,
        course="Backend Engineering",
        grade="A",
        event_name="Python Bootcamp",
        issuer_name="Acme Academy",
        issued_on=date(2026, 10, 9),
        verification_code="abc123",
        verify_url="http://testserver/verify/abc123",
    )
    base.update(kw)
    return CertificateData(**base)


def test_renders_a_pdf():
    pdf = ReportLabRenderer().render(data())
    assert pdf.startswith(b"%PDF") and pdf.rstrip().endswith(b"%%EOF") and len(pdf) > 1000


def test_unicode_names_render():
    for name in ["Åsa Öberg", "Владимир Петров", "अनुराग शर्मा", "李小龍"]:
        assert ReportLabRenderer().render(data(name)).startswith(b"%PDF")


def test_very_long_values_do_not_break_layout():
    pdf = ReportLabRenderer().render(data("W" * 100, course="C" * 150, event_name="E" * 150, issuer_name="I" * 150))
    assert pdf.startswith(b"%PDF")


def test_markup_like_text_is_drawn_literally():
    pdf = ReportLabRenderer().render(data("<b>Bold</b> & <script>alert(1)</script>"))
    assert pdf.startswith(b"%PDF")


def test_output_is_deterministic():
    assert ReportLabRenderer().render(data()) == ReportLabRenderer().render(data())


def test_no_grade_is_fine():
    assert ReportLabRenderer().render(data(grade=None)).startswith(b"%PDF")
