from __future__ import annotations

from decimal import Decimal
from random import Random

from economia_artificial.domain import Agent, Customer, Product, ProductStatus, money, utc_now
from economia_artificial.ledger import Ledger


class MarketError(ValueError):
    pass


class SimulatedMarket:
    """A reproducible market that exposes only public product information."""

    ALLOWED_CATEGORIES = frozenset(
        {"business_analytics", "productivity", "automation", "templates"}
    )

    def __init__(self, ledger: Ledger, seed: int) -> None:
        self._ledger = ledger
        self._random = Random(seed)
        self.products: dict[str, Product] = {}
        self.customers: dict[str, Customer] = {}
        self._next_product_number = 1

    def add_customer(self, customer: Customer) -> None:
        if customer.id in self.customers:
            raise MarketError(f"Customer {customer.id} already exists")
        if customer.budget < 0:
            raise MarketError("Customer budget cannot be negative")
        self.customers[customer.id] = customer

    def create_product(
        self,
        agent_id: str,
        name: str,
        description: str,
        category: str,
    ) -> Product:
        self._validate_product_text(name, description, category)
        product = Product(
            id=f"product-{self._next_product_number}",
            owner_agent_id=agent_id,
            name=name.strip(),
            description=description.strip(),
            category=category,
        )
        self._next_product_number += 1
        self.products[product.id] = product
        return product

    def update_product(
        self,
        agent_id: str,
        product_id: str,
        name: str | None,
        description: str | None,
        category: str | None,
    ) -> Product:
        product = self._owned_product(agent_id, product_id)
        updated_name = product.name if name is None else name.strip()
        updated_description = product.description if description is None else description.strip()
        updated_category = product.category if category is None else category
        self._validate_product_text(updated_name, updated_description, updated_category)
        product.name = updated_name
        product.description = updated_description
        product.category = updated_category
        product.updated_at = utc_now()
        return product

    def publish_product(self, agent_id: str, product_id: str) -> Product:
        product = self._owned_product(agent_id, product_id)
        if product.status not in {ProductStatus.DRAFT, ProductStatus.PAUSED}:
            raise MarketError("Product is already published")
        if product.price is None:
            raise MarketError("A product needs a price before publication")
        product.status = ProductStatus.PUBLISHED
        product.updated_at = utc_now()
        return product

    def set_price(
        self, agent_id: str, product_id: str, price: Decimal | str | int | float
    ) -> Product:
        product = self._owned_product(agent_id, product_id)
        normalized_price = money(price)
        if not Decimal("0.01") <= normalized_price <= Decimal("1000.00"):
            raise MarketError("Product price must be between 0.01 and 1000.00")
        product.price = normalized_price
        product.updated_at = utc_now()
        return product

    def search(self, query: str, category: str | None) -> list[dict[str, object]]:
        normalized_query = query.casefold().strip()
        candidates = self._published_products()
        if category is not None:
            candidates = [product for product in candidates if product.category == category]
        if normalized_query:
            candidates = [
                product
                for product in candidates
                if normalized_query in product.name.casefold()
                or normalized_query in product.description.casefold()
                or normalized_query in product.category.casefold()
            ]
        candidates.sort(key=lambda product: (product.price or Decimal("0"), product.id))
        return [self._public_product(product) for product in candidates[:20]]

    def inspect(self, product_id: str) -> dict[str, object]:
        product = self.products.get(product_id)
        if product is None or product.status != ProductStatus.PUBLISHED:
            raise MarketError("Published product not found")
        return self._public_product(product, detailed=True)

    def overview(self) -> dict[str, object]:
        """Aggregate demand signals; individual customer data remains private."""
        demand_by_category: dict[str, Decimal] = {}
        for customer in self.customers.values():
            for category, need in customer.needs.items():
                demand_by_category[category] = demand_by_category.get(category, Decimal("0")) + need
        active_products_by_category: dict[str, int] = {}
        for product in self._published_products():
            active_products_by_category[product.category] = (
                active_products_by_category.get(product.category, 0) + 1
            )
        return {
            "categories": [
                {
                    "category": category,
                    "demand_index": str(demand),
                    "competition_index": active_products_by_category.get(category, 0),
                }
                for category, demand in sorted(demand_by_category.items())
            ],
            "active_products": len(self._published_products()),
        }

    def advance(self, agents: dict[str, Agent]) -> list[dict[str, object]]:
        """Run one deterministic demand period and settle every completed sale."""
        sales: list[dict[str, object]] = []
        published = self._published_products()
        for customer in self.customers.values():
            compatible = [
                product
                for product in published
                if product.category in customer.needs
                and product.price is not None
                and self._ledger.can_afford(self.customer_account(customer.id), product.price)
            ]
            if not compatible:
                continue
            product = max(
                compatible,
                key=lambda candidate: self._purchase_score(
                    customer, agents[candidate.owner_agent_id], candidate
                ),
            )
            score = self._purchase_score(customer, agents[product.owner_agent_id], product)
            if self._random.random() > float(score):
                continue
            assert product.price is not None
            self._ledger.transfer(
                debit_account=self.customer_account(customer.id),
                credit_account=self.agent_account(product.owner_agent_id),
                amount=product.price,
                transaction_type="PRODUCT_SALE",
                description=f"Customer {customer.id} purchased {product.id}",
                reference_id=product.id,
            )
            product.units_sold += 1
            product.updated_at = utc_now()
            sales.append(
                {
                    "customer_id": customer.id,
                    "product_id": product.id,
                    "amount": str(product.price),
                }
            )
        return sales

    @staticmethod
    def agent_account(agent_id: str) -> str:
        return f"agent:{agent_id}:cash"

    @staticmethod
    def customer_account(customer_id: str) -> str:
        return f"customer:{customer_id}:cash"

    def _purchase_score(self, customer: Customer, agent: Agent, product: Product) -> Decimal:
        assert product.price is not None
        need = customer.needs[product.category]
        customer_budget = max(customer.budget, Decimal("0.01"))
        affordability = max(Decimal("0"), Decimal("1") - (product.price / customer_budget))
        reputation = agent.reputation
        score = (
            need * Decimal("0.65")
            + affordability * (Decimal("0.25") + customer.price_sensitivity * Decimal("0.10"))
            + reputation * customer.reputation_sensitivity * Decimal("0.10")
        )
        return min(Decimal("0.99"), max(Decimal("0"), score.quantize(Decimal("0.0001"))))

    def _owned_product(self, agent_id: str, product_id: str) -> Product:
        product = self.products.get(product_id)
        if product is None:
            raise MarketError("Product not found")
        if product.owner_agent_id != agent_id:
            raise MarketError("Product does not belong to agent")
        return product

    def _published_products(self) -> list[Product]:
        return [
            product
            for product in self.products.values()
            if product.status == ProductStatus.PUBLISHED
        ]

    def _validate_product_text(self, name: str, description: str, category: str) -> None:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise MarketError("Product name must contain 1 to 100 characters")
        if not isinstance(description, str) or not 10 <= len(description.strip()) <= 1_000:
            raise MarketError("Product description must contain 10 to 1000 characters")
        if category not in self.ALLOWED_CATEGORIES:
            raise MarketError("Unsupported product category")

    @staticmethod
    def _public_product(product: Product, detailed: bool = False) -> dict[str, object]:
        result: dict[str, object] = {
            "product_id": product.id,
            "name": product.name,
            "category": product.category,
            "price": str(product.price),
            "public_sales_count": product.units_sold,
        }
        if detailed:
            result["description"] = product.description
        return result
