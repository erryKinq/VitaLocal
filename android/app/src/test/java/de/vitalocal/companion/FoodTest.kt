package de.vitalocal.companion

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FoodTest {
    private val serverDay = """
        {"day": "2026-10-03",
         "meals": [{"index": 0, "name": "Frühstück", "entries": [
                      {"id": "e1", "day": "2026-10-03", "logged_at": "2026-10-03T08:00:00+02:00", "meal_index": 0,
                       "food_id": "f1", "name": "Haferflocken", "brand": null, "amount": 50, "unit": "g", "quantity": 1,
                       "nutrients": {"calories": 185, "proteins": 6.75}, "source": "vitalocal", "editable": true}]},
                   {"index": 1, "name": "Mittagessen", "entries": []},
                   {"index": 2, "name": "Abendessen", "entries": [
                      {"id": "e2", "day": "2026-10-03", "logged_at": "2026-10-03T19:00:00+02:00", "meal_index": 2,
                       "food_id": null, "name": "Kuchen", "amount": null, "unit": null, "quantity": 1,
                       "nutrients": {"calories": 300}, "source": "waistline", "editable": false}]},
                   {"index": 3, "name": "Snacks", "entries": []}],
         "goals": [{"metric": "nutrition.protein", "label": "Protein", "unit": "g", "min_value": 120, "max_value": 180}],
         "waistline_retired": false}
    """.trimIndent()

    private val oats = Food("f1", "Haferflocken", nutrients = mapOf("calories" to 370.0, "proteins" to 13.5),
        portions = listOf(Portion("1 Portion", 50.0)))
    private val day = NutritionDay.parse(JSONObject(serverDay), 1)

    @Test
    fun parsesTheDay() {
        assertEquals(listOf("Frühstück", "Mittagessen", "Abendessen", "Snacks"), day.meals)
        assertEquals(listOf("e1"), day.mealEntries(0).map { it.id })
        assertEquals(485.0 to 0, day.total("calories"))
        assertEquals(6.75 to 1, day.total("proteins")) // the cake declares no protein: counts as 0
        assertEquals(120.0, day.goal("proteins")!!.min!!, 0.0)
        assertEquals(false, day.entries.first { it.id == "e2" }.editable)
        assertEquals("50 g", day.entries.first().amountText)
    }

    @Test
    fun pendingEntriesUseTheServersFormula() {
        val add = PendingOp.AddFoodEntry("o1", FoodEntryIn("e3", "2026-10-03", "2026-10-03T12:00:00+02:00", 1, "f1", 80.0, 2.0))
        val quick = PendingOp.AddFoodEntry("o2", FoodEntryIn("e4", "2026-10-03", "2026-10-03T15:00:00+02:00", 3,
            name = "Riegel", nutrients = mapOf("calories" to 200.0), quantity = 2.0))
        val otherDay = PendingOp.AddFoodEntry("o3", FoodEntryIn("e5", "2026-10-02", "2026-10-02T12:00:00+02:00", 1, "f1", 50.0))
        val v = day.withPending(listOf(add, quick, otherDay)) { if (it == "f1") oats else null }
        val e3 = v.entries.first { it.id == "e3" }
        assertEquals(370.0 * 80 / 100 * 2, e3.nutrients["calories"]!!, 1e-9)
        assertTrue(e3.pending)
        assertEquals("2 × 80 g", e3.amountText)
        assertEquals(400.0, v.entries.first { it.id == "e4" }.kcal!!, 0.0)
        assertNull(v.entries.firstOrNull { it.id == "e5" })

        // a correction scales the snapshot (185 kcal for 50 g → 100 g), a deletion removes
        val upd = PendingOp.UpdateFoodEntry("o4", "e1", "2026-10-03", "2026-10-03T09:00:00+02:00", 1, 100.0, 1.0)
        val v2 = day.withPending(listOf(upd, PendingOp.DeleteFoodEntry("o5", "e2"))) { null }
        assertEquals(370.0, v2.entries.single().kcal!!, 1e-9)
        assertEquals(1, v2.entries.single().mealIndex)
    }

    @Test
    fun foodOpsSurviveTheOutbox() {
        val ops = listOf(
            PendingOp.AddFood("o1", oats.copy(barcode = "4000000000001", favorite = true)),
            PendingOp.UpdateFood("o2", oats.copy(name = "Hafer")),
            PendingOp.AddFoodEntry("o3", FoodEntryIn("e3", "2026-10-03", "2026-10-03T12:00:00+02:00", 1, "f1", 80.0, 2.0)),
            PendingOp.AddFoodEntry("o4", FoodEntryIn("e4", "2026-10-03", "2026-10-03T12:00:00+02:00", 1,
                name = "Riegel", nutrients = mapOf("calories" to 200.0))),
            PendingOp.UpdateFoodEntry("o5", "e1", "2026-10-03", "2026-10-03T09:00:00+02:00", 1, null, 1.5),
            PendingOp.DeleteFoodEntry("o6", "e2"),
            PendingOp.AddBodyEntry("o7", "b1", "2026-10-03T07:00:00+02:00", "2026-10-03", "weight", 64.2),
            PendingOp.AddTemplate("o8", MealTemplate("t1", "Skyr Bowl", listOf(
                TemplateItem("f1", "Haferflocken", 40.0, 1.0, "g", mapOf("calories" to 148.0)),
            ))),
            PendingOp.AddFoodEntry("o9", FoodEntryIn("e9", "2026-10-03", "2026-10-03T08:00:00+02:00", 0, "f1", 40.0,
                templateId = "t1")),
        )
        val back = ops.map { PendingOp.fromJson(JSONObject(it.toJson().toString())) }
        assertEquals(ops, back)
        assertTrue(back.all { it !is PendingOp.Unknown })
        assertEquals(listOf(true, true, true, true, true, true, false, true, true), back.map { it.isFood })
        assertEquals("t1", (ops[8] as PendingOp.AddFoodEntry).entry.toApiJson().getString("template_id"))
        assertEquals(148.0, (back[7] as PendingOp.AddTemplate).template.nutrients["calories"]!!, 0.0)
        // what goes to the server
        val api = (ops[2] as PendingOp.AddFoodEntry).entry.toApiJson()
        assertEquals("f1", api.getString("food_id"))
        assertEquals("android", api.getString("entered_via"))
        assertTrue(!api.has("day"))
    }

    @Test
    fun weightShowsOnTodayWhileQueued() {
        val today = TodayData.empty("2026-10-03").copy(metrics = mapOf("weight" to Metric(null, "kg", 1, null, null)))
        val v = today.withPending(listOf(PendingOp.AddBodyEntry("o", "b", "2026-10-03T07:00:00+02:00", "2026-10-03", "weight", 64.2)))
        assertEquals(64.2, v.metrics["weight"]!!.value!!, 0.0)
    }

    @Test
    fun searchAndBarcodes() {
        val foods = listOf(
            oats.copy(uses = 3, lastUsed = "2026-10-01"),
            Food("f2", "Hafermilch", brand = "Oatly", uses = 9, barcode = "7394376616037"),
            Food("f3", "Haferkekse", favorite = true),
            Food("f4", "Alte Haferflocken", archived = true),
        )
        assertEquals(listOf("f3", "f2", "f1"), searchFoods(foods, "hafer").map { it.id })
        assertEquals(listOf("f2"), searchFoods(foods, "oatly milch").map { it.id })
        assertEquals(listOf("f3", "f1"), searchFoods(foods, "").map { it.id }) // favorites and used ones
        assertTrue(sameBarcode("0012345678905", "12345678905"))
        assertEquals("08:05", parseClock("8:05"))
        assertEquals("21:30", parseClock("2130"))
        assertNull(parseClock("25:00"))
        assertEquals(1.5, parseNum("1,5")!!, 0.0)
    }

    @Test
    fun newOutboxOpsSurviveAStoredRoundTrip() {
        val dish = Food("d1", "Müsli", baseAmount = 1.0, baseUnit = "portion", dish = true,
            components = listOf(Component("f1", "Haferflocken", 60.0, "g")), nutrients = mapOf("calories" to 222.0))
        val tpl = MealTemplate("t1", "Frühstück", listOf(TemplateItem("f1", "Haferflocken", 50.0, 2.0, "g")))
        val ops = listOf(
            PendingOp.UpdateFood("o1", dish), PendingOp.DeleteFood("o2", "f9"),
            PendingOp.UpdateTemplate("o3", tpl), PendingOp.DeleteTemplate("o4", "t1"),
            PendingOp.UpdateFoodEntry("o5", "e2", "2026-10-03", "2026-10-03T19:00:00+02:00", 2, null, 2.0,
                name = "Torte", nutrients = mapOf("calories" to 250.0)),
            PendingOp.UpdateFoodEntry("o6", "e1", "2026-10-03", "2026-10-03T08:00:00+02:00", 0, 60.0, 1.0, recompute = true),
        )
        val back = ops.map { PendingOp.fromJson(JSONObject(it.toJson().toString())) }
        assertEquals(ops, back.map { if (it is PendingOp.UpdateFood) it.copy(food = it.food.copy(updatedAt = null)) else it })
        // a dish sends its ingredients, so the server recomputes it
        assertEquals("f1", dish.toApiJson().getJSONArray("components").getJSONObject(0).getString("food_id"))
    }

    @Test
    fun dishNutrientsAddUpTheIngredients() {
        val milk = Food("f2", "Milch", baseUnit = "ml", nutrients = mapOf("calories" to 64.0, "fat" to 3.5))
        val n = dishNutrients(listOf(Component("f1", "Haferflocken", 60.0), Component("f2", "Milch", 200.0))) {
            mapOf("f1" to oats, "f2" to milk)[it]
        }
        assertEquals(222.0 + 128.0, n["calories"]!!, 1e-9)
        assertEquals(7.0, n["fat"]!!, 1e-9) // oats declare no fat: nothing added
    }

    @Test
    fun templatesFollowTheOutbox() {
        val a = MealTemplate("t1", "A", listOf(TemplateItem("f1", "Haferflocken", 50.0)))
        val b = MealTemplate("t2", "B", listOf(TemplateItem("f1", "Haferflocken", 30.0)))
        val state = FoodState(templates = listOf(a, b))
        val renamed = a.copy(name = "A2")
        val shown = state.templates(listOf(PendingOp.UpdateTemplate("o1", renamed), PendingOp.DeleteTemplate("o2", "t2")))
        assertEquals(listOf("A2"), shown.map { it.name })
    }

    @Test
    fun correctionsOfQuickEntriesAndRecomputedFoods() {
        val quick = PendingOp.UpdateFoodEntry("o1", "e2", "2026-10-03", "2026-10-03T19:00:00+02:00", 2, null, 2.0,
            name = "Torte", nutrients = mapOf("calories" to 250.0))
        val fixedOats = oats.copy(nutrients = mapOf("calories" to 400.0))
        val recompute = PendingOp.UpdateFoodEntry("o2", "e1", "2026-10-03", "2026-10-03T08:00:00+02:00", 0, 50.0, 1.0, recompute = true)
        val d = day.withPending(listOf(quick, recompute)) { if (it == "f1") fixedOats else null }
        val cake = d.entries.first { it.id == "e2" }
        assertEquals("Torte" to 500.0, cake.name to cake.kcal)
        assertEquals(200.0, d.entries.first { it.id == "e1" }.kcal!!, 1e-9)
    }

    @Test
    fun mealsKeepTheOrderEntriesWereAdded() {
        val later = FoodEntryIn("n1", "2026-10-03", "2026-10-03T07:00:00+02:00", 0, foodId = "f1", amount = 30.0)
        val d = day.withPending(listOf(PendingOp.AddFoodEntry("o1", later))) { if (it == "f1") oats else null }
        // logged earlier in the day, but added last: last in the meal
        assertEquals(listOf("e1", "n1"), d.mealEntries(0).map { it.id })
    }
}
