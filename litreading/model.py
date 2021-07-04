import numpy.typing as npt
from typing import Any, Dict, List, Literal, Union

import itertools
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from loguru import logger

from sklearn import base
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.model_selection import GridSearchCV, train_test_split
from sklearn.pipeline import Pipeline

from litreading.config import (
    BASELINE_MODEL_PREDICTION_COL,
    HUMAN_WCPM_COL,
    PREPROCESSING_STEPS,
    SEED,
)
from litreading.preprocessor import LCSPreprocessor
from litreading.utils.evaluation import compute_evaluation_report
from litreading.utils.files import open_file, save_to_file
from litreading.utils.visualization import feature_importance, plot_grid_search


@dataclass
class Dataset:
    X_train_raw: pd.DataFrame
    X_test_raw: pd.DataFrame
    y_train: pd.Series
    y_test: pd.Series
    X_train: pd.DataFrame = field(init=False, default=None)
    X_test: pd.DataFrame = field(init=False, default=None)


class Model:

    _preprocessor = LCSPreprocessor(**PREPROCESSING_STEPS)

    def __init__(
        self,
        estimator: Union[str, BaseEstimator] = "default",
        scaler: Union[str, TransformerMixin] = "default",
        baseline_mode: bool = False,
        verbose: bool = False,
    ) -> None:
        if not isinstance(baseline_mode, bool):
            raise TypeError("baseline_mode must be a boolean")

        self._baseline_mode = baseline_mode
        self.verbose = verbose
        self._scaler = None
        self._estimator = None
        self._model = None
        self._build_model(scaler, estimator)

        self._dataset = None

    @property
    def model(self) -> Pipeline:
        return self._model

    @property
    def preprocessor(self) -> LCSPreprocessor:
        return self._preprocessor

    @property
    def baseline_mode(self) -> bool:
        return self._baseline_mode

    @property
    def dataset(self) -> Dataset:
        if not hasattr(self, "_dataset"):
            raise AttributeError(
                "training and test set not defined. Please start by using self._prepare_train_test_set"
            )
        return self._dataset

    def _build_model(
        self,
        scaler: Union[str, BaseEstimator],
        estimator: Union[str, TransformerMixin],
    ) -> None:
        if self.baseline_mode:
            msg = "Baseline mode -> Instanciating Baseline Model."
            msg += " Any scaler or estimator argument will be ignored."
            msg += "\nThe prediction is the word correct count based on differ list."
            logger.warning(msg)
            self._model = None
        else:
            self._check_estimator(estimator)
            self._check_scaler(scaler)
            self._model = Pipeline(
                [
                    ("scaler", self._scaler),
                    ("estimator", self._estimator),
                ],
                verbose=self.verbose,
            )

    def _check_estimator(self, estimator: Union[str, BaseEstimator]) -> None:
        if isinstance(estimator, str):
            raise NotImplementedError
        if isinstance(estimator, BaseEstimator):
            if base.is_classifier(estimator):
                raise TypeError("estimator must be a sklearn-like regressor")
            self._estimator = estimator
        else:
            raise TypeError("estimator must be either an str or a sklearn.base.BaseEstimator.")

    def _check_scaler(self, scaler: Union[str, TransformerMixin]) -> None:
        if isinstance(scaler, str):
            raise NotImplementedError
        if isinstance(scaler, TransformerMixin):
            if not hasattr(scaler, "fit_transform"):
                raise TypeError("scaler must be a sklearn-like classifier")
            self._scaler = scaler
        else:
            raise TypeError("scaler must be either an str or a sklearn.base.BaseEstimator.")

    def prepare_train_test_set(
        self,
        dataset: pd.DataFrame,
        test_set_size: float = 0.2,
    ) -> None:
        self._dataset = Dataset(
            *train_test_split(
                dataset.drop(columns=[HUMAN_WCPM_COL]),
                dataset[HUMAN_WCPM_COL],
                test_size=test_set_size,
                random_state=SEED,
            )
        )
        # self._test_idx = self.dataset.X_test_raw.index

    def fit(self):
        self._dataset.X_train = self.preprocessor.preprocess_data(
            self.dataset.X_train_raw, verbose=self.verbose
        )
        mask = (
            pd.DataFrame(self.dataset.X_train).isna().any(axis=1)
            | pd.Series(self.dataset.y_train).isna().any()
        )  # HACK
        self._dataset.X_train = self.dataset.X_train[~mask]
        self._dataset.y_train = self.dataset.y_train[~mask]

        if not self.baseline_mode:
            self.model.fit(self.dataset.X_train, self.dataset.y_train)
        return self

    def predict(self, X: pd.DataFrame) -> npt.ArrayLike:
        X_processed = self.preprocessor.preprocess_data(X, verbose=False)
        if self.baseline_mode:
            y_pred = X_processed[BASELINE_MODEL_PREDICTION_COL].values
        else:
            y_pred = self.model.predict(X_processed)
        return y_pred

    def evaluate(self, X: npt.ArrayLike = None, y_true: npt.ArrayLike = None) -> pd.DataFrame:
        if X is None:
            X = self.dataset.X_test_raw
        if y_true is None:
            y_true = self.dataset.y_test

        y_pred = self.predict(X)
        metrics = compute_evaluation_report(y_true, y_pred)
        return metrics

    @staticmethod
    def __format_param_grid(
        mode=Literal["scaler", "estimator"], param_grid: Dict[str, List[Any]] = None
    ) -> Dict[str, List[Any]]:
        if param_grid is not None:
            param_grid = {f"{mode}__{k}": v for k, v in param_grid.items()}
        else:
            param_grid = {}
        return param_grid

    def grid_search(
        self,
        param_grid_scaler: Dict[str, List[Any]] = None,
        param_grid_estimator: Dict[str, List[Any]] = None,
        cv: int = 5,
        verbose: int = 5,
        scoring_metric: str = "r2",
        set_best_model: bool = True,
    ):
        param_grid = {}
        param_grid.update(self.__format_param_grid(mode="scaler", param_grid=param_grid_scaler))
        param_grid.update(
            self.__format_param_grid(mode="estimator", param_grid=param_grid_estimator)
        )
        if len(param_grid) == 0:
            raise ValueError("Please give at least one param to test")

        msg = f"\n{' Model: ' :-^120}\n"
        msg += f"{self.model}\n"
        msg += f"\n{' Params to be tested: ' :-^120}\n"
        msg += "\n".join([f"{key}: {value}" for key, value in param_grid.items()])
        n_combi = len(list(itertools.product(*param_grid.values())))
        msg += f"\n\n# of possible combinations for cross-validation: {n_combi}"
        msg += f"\nMetric for evaluation: {scoring_metric}"
        logger.info(msg)

        while True:
            answer = input("\nContinue with these Cross-validation parameters ? (y/n)")
            if answer not in ["y", "n"]:
                print("Possible answers: 'y' or 'n'")
                continue
            if answer == "y":
                break
            logger.error("Please redefine inputs.")
            return None

        verbose_model = self.model.verbose
        self._model.verbose = False

        grid_search = GridSearchCV(
            estimator=self.model,
            param_grid=param_grid,
            scoring=scoring_metric,
            cv=cv,
            n_jobs=-1,
            verbose=verbose,
        )

        self._dataset.X_train = self.preprocessor.preprocess_data(
            self.dataset.X_train_raw, verbose=verbose > 0
        )
        grid_search.fit(self.dataset.X_train, self.dataset.y_train)

        if set_best_model is True:
            self._model = grid_search.best_estimator_

        self._model.verbose = verbose_model

        return grid_search

    @staticmethod
    def plot_grid_search(
        cv_results: dict,
        x: str,
        y: str = "mean_test_score",
        hue: str = None,
        x_log_scale: bool = False,
    ) -> plt.Figure:
        fig = plot_grid_search(cv_results, x=x, y=y, hue=hue, x_log_scale=x_log_scale)
        return fig

    def plot_feature_importance(self, threshold: float = 0.005):
        fig = feature_importance(
            self.model["estimator"], self.dataset.X_train.columns, threshold=threshold
        )
        return fig

    def plot_scatter(self):
        raise NotImplementedError

    def plot_wcpm_distribution(self):
        raise NotImplementedError

    def plot_train_test_feature_distributions(self):
        raise NotImplementedError

    @classmethod
    def load_from_file(cls, model_filepath: Union[str, Path]) -> Pipeline:
        model_filepath = Path(model_filepath)
        if not model_filepath.is_file():
            raise FileNotFoundError(model_filepath)
        if model_filepath.suffix != ".pkl":
            raise ValueError("Please give a path to pickle file")

        model_ = open_file(model_filepath)

        if not isinstance(model_, Pipeline):
            raise ValueError(
                "Incompatible model: please give a filepath to a sklearn pipeline object"
            )

        scaler = model_.steps[0][1]
        estimator = model_.steps[1][1]
        model = cls(estimator=estimator, scaler=scaler, baseline_mode=False)

        logger.info(f"Model loaded from {model_filepath}: \n{model_}")
        return model

    def save_model(
        self, filepath: Union[str, Path], version: bool = True, overwrite: bool = False
    ) -> None:
        save_to_file(self.model, filepath, version=version, overwrite=overwrite, makedirs=True)
