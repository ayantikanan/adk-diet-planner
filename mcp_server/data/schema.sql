-- Meals database schema. The database rejects bad data itself (CHECK constraints).

CREATE TABLE IF NOT EXISTS meals (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    meal_type   TEXT    NOT NULL CHECK (meal_type IN ('breakfast', 'lunch', 'dinner', 'snack')),
    calories    INTEGER NOT NULL CHECK (calories BETWEEN 50 AND 1500),
    protein_g   REAL    NOT NULL CHECK (protein_g >= 0),
    carbs_g     REAL    NOT NULL CHECK (carbs_g >= 0),
    fat_g       REAL    NOT NULL CHECK (fat_g >= 0),
    vegetarian  INTEGER NOT NULL CHECK (vegetarian IN (0, 1)),
    ingredients TEXT    NOT NULL DEFAULT ''   -- main ingredients, lower case, comma-separated
);

-- Allergens in each meal, one row per allergen. Only known allergens are accepted.
CREATE TABLE IF NOT EXISTS meal_allergens (
    meal_id  INTEGER NOT NULL REFERENCES meals (id) ON DELETE CASCADE,
    allergen TEXT    NOT NULL CHECK (allergen IN
             ('dairy', 'eggs', 'fish', 'shellfish', 'gluten', 'nuts', 'peanuts', 'soy', 'sesame')),
    PRIMARY KEY (meal_id, allergen)
);

-- The meal tool always filters by meal type (and often vegetarian): index for it.
CREATE INDEX IF NOT EXISTS idx_meals_type_vegetarian ON meals (meal_type, vegetarian);
CREATE INDEX IF NOT EXISTS idx_meal_allergens_allergen ON meal_allergens (allergen);
