import pytest
from src.utils.broker_api import UpbitBroker

@pytest.fixture
def broker():
    return UpbitBroker()

def test_format_price(broker):
    # Over 2,000,000
    assert broker._format_price(55000500) == "55001000"
    assert broker._format_price(55000499) == "55000000"
    
    # Under 1,000
    assert broker._format_price(777.7) == "778"
    assert broker._format_price(777.4) == "777"
    
    # Under 10
    assert broker._format_price(5.55) == "5.6"
    assert broker._format_price(5.54) == "5.5"

def test_format_volume(broker):
    assert broker._format_volume(1.23456789) == "1.23456789"
    assert broker._format_volume(1.23) == "1.23"
    assert broker._format_volume(1.0) == "1"
    assert broker._format_volume(0.00000001) == "0.00000001"
    assert broker._format_volume(1) == "1"
    assert broker._format_volume(0) == "0"
