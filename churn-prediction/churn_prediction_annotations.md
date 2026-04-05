# Churn Prediction Notebook — Code Annotations

Explanation of every method, function, class, and definition: what it is, why it was chosen, and why it sits where it does in the pipeline.

---

## Imports & Configuration (Cell 1)

**Why this set of libraries:**
- `pandas` / `numpy` — standard tabular data and numerical ops
- `matplotlib` / `seaborn` — visualization; seaborn's `set_theme()` is called immediately so all subsequent plots share consistent styling
- `sklearn` suite — chosen because it's the de facto standard for classical ML pipelines; all pieces (splitting, scaling, encoding, models, metrics) are API-compatible
- `shap` — model explainability; essential for a business-facing churn model where you need to justify predictions
- `xgboost` — gradient boosting; often the strongest performer on structured/tabular data

**Why placed first:** all downstream cells import from these namespaces; Python requires imports before use.

---

## Data Loading (Cell 2)

```python
url = "..."
df = pd.read_csv(url)
```

`df` is the **raw, untouched** source. Everything downstream derives from it. Keeping `df` immutable (you copy it before mutating) is intentional — lets you restart any step without re-downloading.

---

## `evaluate_model()` function

**Signature:** `evaluate_model(name, model, X_tr, y_tr, X_te, y_te, scaled=True)`

**Why it exists:** All three model blocks (LR, RF, XGBoost) do the same four steps — fit, predict class, predict probability, compute metrics. Extracting this into a function eliminates copy-paste and makes the metric dictionary structure identical across all models, which is what allows `results_df` to work cleanly later.

**Why placed before the model cells:** Python requires a function to be defined before it's called. It's positioned right before Cell 20 (model evaluation setup), which is the earliest point it's needed.

**What it returns:** `(metrics_dict, fitted_model, y_pred, y_proba)` — four values because each caller needs all four for different purposes:
- `metrics_dict` → appended to `results` list
- `fitted_model` → stored in `trained_models` dict
- `y_pred` → used for confusion matrix
- `y_proba` → used for ROC/PR curves and SHAP

---

## Data Cleaning Chain

| Variable | Purpose | Why here |
|---|---|---|
| `numeric_cols` | `['tenure', 'MonthlyCharges', 'TotalCharges']` | Named once, reused in correlation and scaling steps |
| `cat_features` | Subset for EDA plots | Separate from the full categorical list used in encoding |
| `Churn_numeric` | Integer version of target for correlation | Correlation requires numeric types; created temporarily for EDA, not kept |
| `corr_matrix` | Pearson correlation of numerics + target | Placed after numeric coercion, before feature engineering decisions |
| `data = df.copy()` | Mutable working copy | Isolates mutations from the raw `df` |

**`TotalCharges` coercion to numeric** happens twice — once in EDA context (on `df`), once in the modeling context (on `data`). This is because EDA uses `df` (raw) and modeling uses `data` (cleaned copy).

---

## Encoding Variables

```python
le = LabelEncoder()
binary_cols  # 2-unique-value columns → LabelEncoder
multi_cols   # 3+ unique values → pd.get_dummies(drop_first=True)
```

**Why split binary vs multi:**
- `LabelEncoder` works for binary (0/1 is ordinal-safe with 2 classes).
- For multi-class categoricals, one-hot encoding via `get_dummies` avoids implying a false ordinal relationship (e.g., "Fiber optic" > "DSL" is meaningless numerically).
- `drop_first=True` removes one dummy column per feature to avoid perfect multicollinearity — critical for Logistic Regression.

---

## Train/Test Split

```python
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)
```

**`stratify=y`** — because churn datasets are class-imbalanced (~26% churn). Without stratification, the test set might have a very different churn rate by chance, making evaluation metrics misleading.

**`random_state=42`** — reproducibility; any fixed integer works, 42 is conventional.

---

## `StandardScaler`

```python
scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)   # fit + transform
X_test_scaled  = scaler.transform(X_test)         # transform only — NOT fit_transform
```

**Why fit only on train:** fitting on test data would leak information about the test distribution into the scaler — a form of data leakage. The test set must be transformed using the train set's mean and standard deviation only.

**Why scaled versions exist alongside unscaled:** Logistic Regression is sensitive to feature scale (gradient descent converges poorly with unscaled features). Random Forest and XGBoost are tree-based and scale-invariant, so they use the engineered but unscaled features.

---

## Feature Engineering (Cell 18)

```python
ChargesPerTenure = MonthlyCharges / (tenure + 1)
ServiceCount     = count of 'Yes' across 8 service columns
IsNewCustomer    = (tenure <= 6).astype(int)
HasStreaming     = (StreamingTV == 'Yes') | (StreamingMovies == 'Yes')
```

**Why these features:**
- `ChargesPerTenure` — captures "value density"; high monthly charges with low tenure is a churn signal
- `+1` in denominator — avoids division-by-zero for tenure=0 customers
- `ServiceCount` — summarizes product stickiness; more services = higher switching cost = lower churn likelihood
- `IsNewCustomer` — early tenure (≤6 months) is the highest churn risk window; binarizing creates a sharp split tree models can exploit
- `HasStreaming` — streaming bundles are associated with retention in telecom data

**Why placed after EDA and before model training:** feature engineering requires understanding what the data looks like (EDA), and the engineered features feed directly into the models.

---

## Model Choices

### `LogisticRegression(class_weight='balanced', max_iter=1000)`
- **Why LR:** interpretable baseline; coefficients directly show feature direction and magnitude
- **`class_weight='balanced'`** — automatically adjusts loss weights to compensate for the ~26/74% class imbalance; equivalent to upweighting the minority (churn) class
- **`max_iter=1000`** — default 100 often fails to converge on this dataset's dimensionality

### `RandomForestClassifier(n_estimators=200, max_depth=10, min_samples_leaf=5, class_weight='balanced')`
- **Why RF:** handles non-linear interactions without manual feature engineering; provides feature importance natively
- **`max_depth=10`, `min_samples_leaf=5`** — regularization to prevent overfitting; unconstrained trees on ~7k rows would overfit
- **`n_jobs=-1`** — uses all CPU cores; no correctness implication, just speed

### `XGBClassifier(scale_pos_weight=neg/pos, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8)`
- **Why XGB:** gradient boosting typically outperforms both LR and RF on tabular data; handles imbalance, missing values, and feature interactions natively
- **`scale_pos_weight=neg_count/pos_count`** — XGBoost's equivalent of `class_weight='balanced'`; computed dynamically from the training split so it stays correct if the split changes
- **`subsample=0.8`, `colsample_bytree=0.8`** — stochastic regularization; each tree sees 80% of rows and 80% of features, reducing correlation between trees and preventing overfitting

---

## `results` list + `trained_models` dict (Cell 20)

```python
results = []
trained_models = {}
```

**Why placed before the model cells:** they're accumulators. Each model cell appends its metrics dict to `results` and stores the fitted model in `trained_models`. This pattern lets Cell 29 build `results_df` in one line from all three models' outputs without any extra merging logic.

---

## Cross-Validation (Cell 32)

```python
cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
```

**Why `StratifiedKFold`:** same rationale as `stratify=y` in `train_test_split` — preserves class proportions in each fold so minority-class performance isn't distorted.

**Why 5 folds:** standard tradeoff between variance (more folds = more stable estimate) and compute cost.

**Why placed after main model evaluation:** cross-validation is a secondary validation step to confirm test-set results aren't a lucky split. It doesn't inform model selection here — it validates it.

---

## SHAP Explainability (Cell 33)

```python
explainer = shap.TreeExplainer(xgb_model)
shap_values = explainer.shap_values(X_test_fe)
```

**Why `TreeExplainer`:** purpose-built for tree-based models (XGBoost, RF); computes exact Shapley values in polynomial time using the tree structure. The generic `KernelExplainer` works on any model but is orders of magnitude slower and approximate.

**Why on XGBoost specifically:** it's the best-performing model, so explaining XGBoost's decisions is most actionable for the business.

**Why placed after training:** SHAP requires a fitted model object; it must come after `evaluate_model()` returns the trained `xgb_model`.

---

## Threshold Optimization (Cell 37)

```python
precisions, recalls, thresholds = precision_recall_curve(y_test_fe, xgb_proba)
f1_scores = 2 * (precisions * recalls) / (precisions + recalls + 1e-9)
best_threshold = thresholds[np.argmax(f1_scores)]
```

**Why this matters:** the default 0.5 probability threshold is rarely optimal for imbalanced problems. In churn prediction, a false negative (missing a churner) is typically more costly than a false positive (sending an unnecessary retention offer). Optimizing threshold on F1 finds the precision/recall balance that maximizes both.

**`+1e-9`** — numerical stability guard; prevents division by zero when precision + recall = 0 at an extreme threshold.

**Why placed last among model steps:** threshold tuning is a post-training decision; it operates on the already-computed `xgb_proba` from `evaluate_model()`.

---

## Risk Report (Cell 38)

```python
risk_report['RiskTier'] = pd.cut(
    xgb_proba,
    bins=[0, 0.3, 0.6, 1.0],
    labels=['Low', 'Medium', 'High']
)
tier_summary = risk_report.groupby('RiskTier').agg(...)
```

**Why `pd.cut` instead of a model output:** converts continuous probability into business-actionable buckets. A retention team can't act on "0.73 probability" but can prioritize a "High Risk" customer. The bins (0.3, 0.6) are domain-conventional starting points; they can be tuned to match the actual cost ratio of false positives vs. false negatives.

**Why placed last:** it's the deliverable — the output artifact the business would actually consume. Every prior step (data cleaning, encoding, feature engineering, model training, threshold optimization) feeds into it.

---

## Top Risk Customers (Cell 39)

```python
top_risk = risk_report.sort_values('ChurnProbability', ascending=False).head(10)
```

Sorts the full test set by raw probability descending and returns the 10 highest-risk customers. Used as a quick operational handoff — these are the customers a retention campaign should target first.
