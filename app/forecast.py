"""Demand forecasting (predictive analytics).

Extension point: `forecast_rooms` takes the observed rooms sold per day and the
rooms already booked for future days, and returns expected rooms sold. To plug
in a stronger model (seasonality, events, ML), replace this function with one
of the same signature; the Forecast page and anything else calling it keep
working unchanged.
"""
from datetime import timedelta
from statistics import mean

HISTORY_WEEKS = 8


def forecast_rooms(history, on_books, future_days):
    """Expected rooms sold per future day.

    history:     {date: rooms sold} for past days
    on_books:    {date: rooms already reserved} for future days
    future_days: list of dates to predict

    Baseline: average of the same weekday over the last HISTORY_WEEKS weeks,
    never below what is already on the books.
    """
    result = {}
    for day in future_days:
        same_weekday = [history[d] for d in history if d.weekday() == day.weekday()
                        and day - timedelta(weeks=HISTORY_WEEKS) <= d < day]
        baseline = mean(same_weekday) if same_weekday else 0
        result[day] = max(baseline, on_books.get(day, 0))
    return result
