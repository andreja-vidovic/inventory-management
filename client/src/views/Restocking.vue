<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrencyWithDecimals } from '../utils/currency'

const { t, currentLocale, currentCurrency, translateProductName } = useI18n()

const DEBOUNCE_MS = 250

const loading = ref(true)
const error = ref(null)
const submitError = ref(null)
const submitting = ref(false)
const confirmation = ref(null)

// Slider value is always in USD; conversion happens only for display.
const budget = ref(0)
const maxBudget = ref(0)
const recommendation = ref({ items: [], total_cost: 0, remaining_budget: 0 })
// True once an order succeeded, until the user moves the slider again.
const orderPlaced = ref(false)

const sliderMax = computed(() => Math.ceil(maxBudget.value))

const formatMoney = (amount, decimals = 0) =>
  formatCurrencyWithDecimals(amount, currentCurrency.value, decimals)

const formatDate = (value) => {
  const d = new Date(value)
  if (!value || isNaN(d.getTime())) return '-'
  return d.toLocaleDateString(currentLocale.value === 'ja' ? 'ja-JP' : 'en-US', {
    year: 'numeric', month: 'short', day: 'numeric'
  })
}

const canPlaceOrder = computed(() =>
  recommendation.value.items.length > 0 && !submitting.value && !orderPlaced.value
)

// Stale-response guard: each request gets an increasing id; only the latest
// request is allowed to write state, so a slow earlier response can't
// overwrite a newer one.
let requestSeq = 0
const fetchRecommendations = async (value) => {
  const seq = ++requestSeq
  try {
    const data = await api.getRestockRecommendations(value)
    if (seq !== requestSeq) return
    recommendation.value = data
    error.value = null
  } catch (err) {
    if (seq !== requestSeq) return
    console.error(err)
    error.value = t('restocking.loadError')
  }
}

let debounceTimer = null
const onSliderInput = () => {
  // Moving the slider re-arms the order button and dismisses old messages.
  orderPlaced.value = false
  submitError.value = null
  confirmation.value = null
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => fetchRecommendations(budget.value), DEBOUNCE_MS)
}

const placeOrder = async () => {
  // Guard against double clicks before the button re-renders as disabled.
  if (!canPlaceOrder.value) return
  clearTimeout(debounceTimer)
  submitting.value = true
  submitError.value = null
  confirmation.value = null
  // Invalidate any in-flight recommendation fetch so it can't repopulate the view.
  requestSeq++
  const snapshot = recommendation.value
  try {
    const order = await api.placeRestockOrder({
      budget: snapshot.budget,
      items: snapshot.items.map(i => ({ sku: i.sku, quantity: i.quantity }))
    })
    orderPlaced.value = true
    // Reset to a clean slate: the planned items are now ordered, so leaving
    // them (and the slider) in place would invite a duplicate order.
    budget.value = 0
    recommendation.value = { items: [], total_cost: 0, remaining_budget: 0, budget: 0 }
    confirmation.value = t('restocking.confirmation', {
      orderNumber: order.order_number,
      days: order.lead_time_days,
      arrival: formatDate(order.expected_arrival)
    })
  } catch (err) {
    // Keep the recommendation so the user can retry.
    console.error(err)
    submitError.value = err?.response?.data?.detail || t('restocking.submitError')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    // Fetch once with budget 0 to learn the slider's upper bound.
    const seq = ++requestSeq
    const data = await api.getRestockRecommendations(0)
    if (seq === requestSeq) recommendation.value = data
    maxBudget.value = data.max_budget
  } catch (err) {
    console.error(err)
    error.value = t('restocking.loadError')
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(() => {
  clearTimeout(debounceTimer)
  requestSeq++
})
</script>

<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error && maxBudget === 0" class="error">{{ error }}</div>
    <div v-else>
      <div class="card slider-card">
        <label class="slider-label" for="budget-slider">
          <span>{{ t('restocking.budgetSlider') }}</span>
          <strong>{{ formatMoney(budget) }}</strong>
        </label>
        <input
          id="budget-slider"
          v-model.number="budget"
          class="budget-slider"
          type="range"
          min="0"
          :max="sliderMax"
          step="1"
          @input="onSliderInput"
        />
        <div class="slider-scale">
          <span>{{ formatMoney(0) }}</span>
          <span>{{ formatMoney(sliderMax) }}</span>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.budget') }}</div>
          <div class="stat-value">{{ formatMoney(budget) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.plannedSpend') }}</div>
          <div class="stat-value">{{ formatMoney(recommendation.total_cost, 2) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.remaining') }}</div>
          <div class="stat-value">{{ formatMoney(recommendation.remaining_budget, 2) }}</div>
        </div>
      </div>

      <div v-if="error" class="error">{{ error }}</div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.title') }} ({{ recommendation.items.length }})</h3>
          <button
            type="button"
            class="place-order-btn"
            :disabled="!canPlaceOrder"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.submitting') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="confirmation" class="confirmation" role="status">{{ confirmation }}</div>
        <div v-if="submitError" class="error" role="alert">{{ submitError }}</div>

        <div v-if="recommendation.items.length === 0" class="empty-note">{{ t('restocking.noItems') }}</div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.name') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th class="num">{{ t('restocking.table.quantity') }}</th>
                <th class="num">{{ t('restocking.table.unitCost') }}</th>
                <th class="num">{{ t('restocking.table.lineTotal') }}</th>
                <th class="num">{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in recommendation.items" :key="item.sku">
                <td><strong>{{ item.sku }}</strong></td>
                <td>
                  {{ translateProductName(item.name) }}
                  <div v-if="item.quantity < item.requested_quantity" class="reduced-note">
                    {{ t('restocking.reduced', { requested: item.requested_quantity, quantity: item.quantity }) }}
                  </div>
                </td>
                <td><span :class="['badge', item.trend]">{{ t(`trends.${item.trend}`) }}</span></td>
                <td class="num">{{ item.quantity }}</td>
                <td class="num">{{ formatMoney(item.unit_cost, 2) }}</td>
                <td class="num">{{ formatMoney(item.line_total, 2) }}</td>
                <td class="num">{{ t('restocking.days', { count: item.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.slider-card {
  margin-bottom: 1.5rem;
  display: grid;
  gap: 0.75rem;
}

.slider-label {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  color: #64748b;
  font-size: 0.875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.slider-label strong {
  color: #0f172a;
  font-size: 1.25rem;
  text-transform: none;
}

.slider-scale {
  display: flex;
  justify-content: space-between;
  color: #64748b;
  font-size: 0.813rem;
}

.budget-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
  outline: none;
  cursor: pointer;
}

.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #0f172a;
  border: 2px solid white;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.4);
}

.budget-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #0f172a;
  border: 2px solid white;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.4);
}

.budget-slider:focus-visible {
  outline: 2px solid #64748b;
  outline-offset: 4px;
}

.place-order-btn {
  background: #0f172a;
  color: white;
  border: none;
  border-radius: 8px;
  padding: 0.5rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
}

.place-order-btn:hover:not(:disabled) {
  background: #1e293b;
}

.place-order-btn:disabled {
  background: #e2e8f0;
  color: #64748b;
  cursor: not-allowed;
}

.confirmation {
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-left: 4px solid #16a34a;
  border-radius: 6px;
  color: #0f172a;
  padding: 0.75rem 1rem;
  margin-bottom: 1rem;
  font-size: 0.938rem;
}

.empty-note {
  color: #64748b;
  font-size: 0.938rem;
  padding: 0.5rem 0;
}

.reduced-note {
  color: #b45309;
  font-size: 0.813rem;
  margin-top: 0.25rem;
}

.num {
  text-align: right;
}
</style>
