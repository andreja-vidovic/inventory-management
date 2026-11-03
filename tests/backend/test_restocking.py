"""
Tests for restocking API endpoints.
"""
import pytest

from mock_data import submitted_orders


@pytest.fixture(autouse=True)
def reset_submitted_orders():
    """Submitted orders live in a shared in-memory list, so clear it around every test."""
    submitted_orders.clear()
    yield
    submitted_orders.clear()


def get_max_budget(client):
    return client.get("/api/restocking/recommendations?budget=0").json()["max_budget"]


class TestRestockRecommendations:
    """Test suite for the restock recommendation endpoint."""

    def test_zero_budget_returns_no_items(self, client):
        """Test that a zero budget recommends nothing."""
        response = client.get("/api/restocking/recommendations?budget=0")
        assert response.status_code == 200

        data = response.json()
        assert data["items"] == []
        assert data["total_cost"] == 0
        assert data["remaining_budget"] == 0
        assert data["max_budget"] > 0

    def test_negative_budget_rejected(self, client):
        """Test that a negative budget returns 400."""
        response = client.get("/api/restocking/recommendations?budget=-1")
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_budget_at_max_restocks_everything_without_reductions(self, client):
        """Test that the slider maximum fully covers every shortfall."""
        max_budget = get_max_budget(client)
        data = client.get(f"/api/restocking/recommendations?budget={max_budget}").json()

        assert len(data["items"]) > 0
        for item in data["items"]:
            assert item["quantity"] == item["requested_quantity"]
        assert abs(data["total_cost"] - max_budget) < 0.01

    def test_mid_budget_flags_reduced_lines(self, client):
        """Test that lines cut to fit the budget have quantity below requested_quantity."""
        max_budget = get_max_budget(client)
        data = client.get(f"/api/restocking/recommendations?budget={max_budget / 2}").json()

        reduced = [i for i in data["items"] if i["quantity"] < i["requested_quantity"]]
        assert len(reduced) > 0

    def test_recommendations_never_exceed_budget(self, client):
        """Test that total cost stays within budget for a range of budgets."""
        max_budget = get_max_budget(client)
        for fraction in (0.05, 0.25, 0.5, 0.75, 1.0):
            budget = round(max_budget * fraction, 2)
            data = client.get(f"/api/restocking/recommendations?budget={budget}").json()

            assert data["total_cost"] <= budget + 0.001
            assert data["remaining_budget"] >= -0.001
            line_sum = sum(i["line_total"] for i in data["items"])
            assert abs(line_sum - data["total_cost"]) < 0.01

    def test_increasing_trend_items_come_first(self, client):
        """Test ordering: increasing-trend items are listed before other trends."""
        max_budget = get_max_budget(client)
        items = client.get(f"/api/restocking/recommendations?budget={max_budget}").json()["items"]

        trends = [i["trend"] == "increasing" for i in items]
        # Once a non-increasing item appears, no increasing item may follow
        assert trends == sorted(trends, reverse=True)

    def test_items_without_shortfall_excluded(self, client):
        """Test that items whose stock already covers forecast demand are not recommended."""
        max_budget = get_max_budget(client)
        items = client.get(f"/api/restocking/recommendations?budget={max_budget}").json()["items"]

        # MTR-304 has more stock than forecast demand
        assert "MTR-304" not in [i["sku"] for i in items]


class TestRestockOrders:
    """Test suite for submitting and listing restock orders."""

    def _recommend(self, client, fraction=0.5):
        budget = round(get_max_budget(client) * fraction, 2)
        data = client.get(f"/api/restocking/recommendations?budget={budget}").json()
        return budget, data["items"]

    def test_create_order_returns_201(self, client):
        """Test that a valid order is created."""
        budget, items = self._recommend(client)
        payload = {
            "budget": budget,
            "items": [{"sku": i["sku"], "quantity": i["quantity"]} for i in items],
        }

        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"].startswith("RST-")
        assert len(order["items"]) == len(items)
        assert order["total_cost"] <= budget

    def test_order_lead_time_is_max_of_lines(self, client):
        """Test that the order lead time is the longest line lead time."""
        budget, items = self._recommend(client, fraction=1.0)
        payload = {
            "budget": budget,
            "items": [{"sku": i["sku"], "quantity": i["quantity"]} for i in items],
        }

        order = client.post("/api/restocking/orders", json=payload).json()
        assert order["lead_time_days"] == max(i["lead_time_days"] for i in items)
        assert order["expected_arrival"] > order["submitted_at"]

    def test_empty_order_rejected(self, client):
        """Test that an order without items returns 400."""
        response = client.post("/api/restocking/orders", json={"budget": 1000, "items": []})
        assert response.status_code == 400

    def test_over_budget_order_rejected(self, client):
        """Test that an order whose total exceeds the budget returns 400."""
        response = client.post(
            "/api/restocking/orders",
            json={"budget": 1, "items": [{"sku": "WDG-001", "quantity": 10}]},
        )
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()

    def test_unknown_sku_rejected(self, client):
        """Test that an item not available for restocking returns 400."""
        response = client.post(
            "/api/restocking/orders",
            json={"budget": 100000, "items": [{"sku": "NOPE-999", "quantity": 1}]},
        )
        assert response.status_code == 400

    def test_invalid_quantity_rejected(self, client):
        """Test that zero or above-shortfall quantities return 400."""
        for quantity in (0, -5, 100000):
            response = client.post(
                "/api/restocking/orders",
                json={"budget": 10000000, "items": [{"sku": "WDG-001", "quantity": quantity}]},
            )
            assert response.status_code == 400

    def test_duplicate_sku_rejected(self, client):
        """Test that repeating a SKU in one order returns 400."""
        response = client.post(
            "/api/restocking/orders",
            json={
                "budget": 100000,
                "items": [{"sku": "WDG-001", "quantity": 1}, {"sku": "WDG-001", "quantity": 1}],
            },
        )
        assert response.status_code == 400

    def test_list_orders_newest_first(self, client):
        """Test that submitted orders are listed with the newest first."""
        assert client.get("/api/restocking/orders").json() == []

        for _ in range(2):
            client.post(
                "/api/restocking/orders",
                json={"budget": 100000, "items": [{"sku": "WDG-001", "quantity": 1}]},
            )

        orders = client.get("/api/restocking/orders").json()
        assert len(orders) == 2
        assert orders[0]["id"] == "2"
        assert orders[1]["id"] == "1"
