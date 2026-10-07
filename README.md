# App - GAMS Code Generator for Journal (Middle + Last Mile)

A Streamlit application that generates formatted GAMS models for multi-period middle-mile and last-mile routing experiments.

## Run locally

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

The generated model uses the requested GAMS naming and formatting convention, including `n`, `p`, `wcd`, `cd`, `wd`, `x`, `Q`, `R`, `Inv`, and `B`.

## Data profiles

The **Data** tab lets you save named profiles with minimum and maximum values for the generated travel-time table and service durations. Custom profiles are available in both the single-model and batch generators; their values are reproducible with random seed 42. The built-in **Default** profile preserves the existing geometry-based table and service data.

The batch generator also has optional service-time iterations, disabled by default to preserve existing batch output. Each iteration adds the configured unit cumulatively to every service duration; optionally, the same cumulative amount is added to every off-diagonal travel-time table value.