import certifi
from neo4j import TrustCustomCAs

from explore import graphdb


def test_driver_args_tls_uri_uses_certifi():
    uri, config = graphdb._driver_args("neo4j+s://abc.databases.neo4j.io")

    assert uri == "neo4j://abc.databases.neo4j.io"
    assert config["encrypted"] is True
    assert isinstance(config["trusted_certificates"], TrustCustomCAs)
    assert config["trusted_certificates"].certs == (certifi.where(),)


def test_driver_args_plain_uri_unchanged():
    assert graphdb._driver_args("bolt://localhost:7687") == ("bolt://localhost:7687", {})


def test_driver_args_self_signed_uri_unchanged():
    # +ssc 는 인증서를 검증하지 않는 체계라 그대로 둔다
    assert graphdb._driver_args("neo4j+ssc://host:7687") == ("neo4j+ssc://host:7687", {})
