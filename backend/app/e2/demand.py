"""Synthetic customer counts and recipe quantities; no replenishment decisions."""

import math
import random
from decimal import Decimal

from app.domain.units import to_base


def recipe_demand(
    recipes: list[dict], weekday: int, rng: random.Random, base_unit: str
) -> list[dict]:
    details = []
    for recipe in recipes:
        centre = recipe["weekday_servings"][weekday]
        if recipe.get("customer_model") == "POISSON":
            if centre <= 0 or centre > 100:
                raise ValueError("Poisson customer mean must be in (0,100]")
            limit, product, count = math.exp(-centre), 1.0, 0
            while product > limit:
                product *= rng.random()
                count += 1
            servings = count - 1
        else:
            jitter = recipe["customer_jitter"]
            servings = centre + rng.randint(-jitter, jitter)
        if servings < 0:
            raise ValueError("Negative recipe serving count")
        if "eggs_per_serving" in recipe:
            quantity = recipe["eggs_per_serving"]
            if base_unit != "unidad" or type(quantity) is not int:
                raise ValueError("Legacy egg recipes require integer unidad")
            details.append(
                {
                    "recipe": recipe["name"],
                    "servings": servings,
                    "eggs_per_serving": quantity,
                    "demand_quantity": servings * quantity,
                }
            )
        else:
            quantity = to_base(Decimal(recipe["quantity_per_serving"]), recipe["unit"], base_unit)
            details.append(
                {
                    "recipe": recipe["name"],
                    "servings": servings,
                    "quantity_per_serving": str(quantity),
                    "base_unit": base_unit,
                    "demand_quantity": quantity * servings,
                }
            )
    return details
