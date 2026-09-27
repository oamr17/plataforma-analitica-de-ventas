"""Contrato CSV sin base de datos ni originales como fixtures."""

import hashlib

import pytest

from sales_analytics import extract


def test_raw_fields_and_logical_records():
    raw = b'Key,Text,Value\r\n001,"a, b\r\nc ""quote""", NA \r\n002,,oops\r\n'
    assert list(extract.iter_records(raw, ("Key", "Text", "Value"), "utf-8")) == [
        (1, ["001", 'a, b\r\nc "quote"', " NA "]),
        (2, ["002", "", "oops"]),
    ]


@pytest.mark.parametrize("encoding,text", [("cp1252", "José €"), ("utf-8", "東京 €")])
def test_encoding(encoding, text):
    raw = ("Name\n" + text + "\n").encode(encoding)
    assert list(extract.iter_records(raw, ("Name",), encoding)) == [(1, [text])]


@pytest.mark.parametrize(
    "raw,ordinal",
    [
        (b"", None),
        (b"Wrong\nx\n", None),
        (b"A,A\nx,y\n", None),
        (b"A,B\nx\n", 1),
        (b'A,B\n"unterminated,y\n', 1),
        (b"A,B\nx,y,z\n", 1),
        (b"A,B\n\n", 1),
        (b"A,B\n\xff,x\n", None),
    ],
)
def test_invalid_structure(raw, ordinal):
    with pytest.raises(extract.SourceError) as caught:
        list(extract.iter_records(raw, ("A", "B"), "utf-8"))
    assert caught.value.ordinal == ordinal


def test_snapshot_hash_and_change_detection(tmp_path):
    path = tmp_path / "source.csv"
    path.write_bytes(b"A\n001\n")
    raw, fingerprint = extract.read_snapshot(path)
    assert (
        hashlib.sha256(raw).hexdigest() == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    extract.verify_unchanged(path, raw, fingerprint)
    path.write_bytes(b"A\n002\n")
    with pytest.raises(extract.SourceError, match="modificado"):
        extract.verify_unchanged(path, raw, fingerprint)


def test_unreadable_file(tmp_path, monkeypatch):
    def denied(self):
        raise PermissionError("fixture")

    monkeypatch.setattr(type(tmp_path), "read_bytes", denied)
    path = tmp_path / "denied.csv"
    path.touch()
    with pytest.raises(extract.SourceError, match="leer"):
        extract.read_snapshot(path)


def test_missing_file(tmp_path):
    with pytest.raises(extract.SourceError, match="leer"):
        extract.read_snapshot(tmp_path / "missing.csv")


def test_change_while_reading_is_detected(tmp_path, monkeypatch):
    path = tmp_path / "changing.csv"
    path.write_bytes(b"A\n001\n")
    original = type(path).read_bytes

    def changing_read(self):
        raw = original(self)
        self.write_bytes(raw + b"002\n")
        return raw

    monkeypatch.setattr(type(path), "read_bytes", changing_read)
    with pytest.raises(extract.SourceError, match="modificado"):
        extract.read_snapshot(path)


def test_cp1252_bytes_are_not_silently_decoded_as_utf8():
    with pytest.raises(extract.SourceError, match="Codificación"):
        list(
            extract.iter_records("Name\nJosé €\n".encode("cp1252"), ("Name",), "utf-8")
        )
