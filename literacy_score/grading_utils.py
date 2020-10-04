import os
import errno
import logging

import numpy as np
import pandas as pd

import joblib

import ast  # preprocessing ast to litteral
import re  # preprocessing
from num2words import num2words  # preprocessing 
import string # preprocessing punctuation
import difflib

# from literacy_score.config import DATA_PATH, MODEL_PATH, DEFAULT_MODEL
DATA_PATH = './data/'
MODEL_PATH = './literacy_score/models/'

def open_file(file_path, sep = ';'):
    _, extension = file_path.rsplit(".", 1)
    if not os.path.exists(file_path):
        raise FileNotFoundError
    if extension == 'csv':
        f = pd.read_csv(file_path, sep=sep)
    else:
        with open(file_path, 'rb') as f:
            f = joblib.load(f)
    return f


def save_file(file, path, file_name, replace=False):
    """ save file with or without replacing previous versions, in cv or pkl
    input: file: python model or df to save
            path: path to save to
            file_name: name to give to the file, including extension
            replace: False if you do not want to delete and replace previous file with same name
    """
    if path[-1] != "/":
        path += "/"
    if not os.path.exists(path):
        raise FileNotFoundError
    file_name, extension = file_name.split(".")
    if replace:
        try:
            os.remove(file_name)
        except OSError: pass
    else:
        i = 0
        while os.path.exists(path + ".".join((file_name + '_{:d}'.format(i), extension))):
            i += 1
        file_name += '_{:d}'.format(i)
    if extension == 'csv':
        file.to_csv(path + ".".join((file_name, extension)), index=False, sep=';', encoding='utf-8')
    else:
        joblib.dump(file, path + ".".join((file_name, extension)), compress = 1)


def avg_length_of_words(s, sep = " "):
    """ takes a string s and gives the avg length of words in it
    """
    s = s.split(sep)
    n = len(s)
    if n == 0:
        return 0
    return sum(len(word) for word in s) / n


def compare_text(string_a, string_b, split_car = " "):
    """ compare string a and b split by split_care, default split by word, remove text surplus at the end
    """
    differ_list = difflib.Differ().compare(str(string_a).split(split_car), str(string_b).split(split_car))
    differ_list = list(differ_list)
    
    # if a lot characters at the end were added or removed from prompt
    # then delete them from differ list 
    to_be_removed = differ_list[-1][0]
    if to_be_removed != " ":
        while differ_list[-1][0] == to_be_removed and len(differ_list) >= 1:
            differ_list.pop()
    return differ_list


def get_errors_dict(differ_list):
    """ computes number of correct, added, removed, replaced words in
     the difflib differ list and computes the list of replaced words detected 
    """
    counter = 0
    errors_dict = {'prompt': [], 'transcript': []}
    skip_next = 0
    n = len(differ_list)
    add = 0
    sub = 0
    for i, word in enumerate(differ_list):
        if skip_next > 0:
            skip_next -= 1
            pass  # when the word has already been added to the error dict
        if word[0] == " ":
            counter += 1  # + 1 if word correct 
        elif i < n - 2:  # keep track of errors and classify them later
            if word[0] == "+":
                add += 1
            elif word[0] == "-":
                sub += 1
            j = 1
            while i+j < n and differ_list[i + j][0] == "?":  # account for ? in skip_next
                j += 1
            plus_minus = (word[0] == "+" and differ_list[i + j][0] == "-")
            minus_plus = (word[0] == "-" and differ_list[i + j][0] == "+")
            skip_next = (plus_minus or minus_plus) * j
            if plus_minus:
                errors_dict['prompt'] += [word.replace("+ ", "")]
                errors_dict['transcript'] += [differ_list[i + j].replace("- ", "")]
            elif minus_plus:
                errors_dict['prompt'] += [word.replace("- ", "")]
                errors_dict['transcript'] += [differ_list[i + j].replace("+ ", "")]
    replaced = len(errors_dict['prompt'])
    return counter, add, sub, replaced, errors_dict


class Dataset():
    def __init__(self,
                df,
                prompt_col = 'prompt',
                asr_col = 'asr_transcript',
                duration_col = 'scored_duration',
                human_wcpm_col = "human_wcpm"
                ):
        self.data_raw = df
        self.data = self.data_raw.copy()
        self.prompt_col = prompt_col
        self.asr_col = asr_col
        self.duration_col = duration_col
        self.human_wcpm_col = human_wcpm_col

    def get_data(self):
        return self.data

    def get_features(self):
        return self.features

    def save_data(self, filename, path = DATA_PATH):
        save_file(self.data, path, filename, replace = False)

    def print_row(self, col_names=[], index = -1):
        if len(col_names) == 0:
            col_names = self.data.columns
        if index != -1:
            for col in col_names:
                print(col)
                print(self.data[col].iloc[index])
                print("\n")
        else:
            print(self.data[col_names]) 

    def preprocess_data(self,
                        lowercase = True,
                        punctuation_free = True,
                        convert_num2words = True,
                        asr_string_recomposition = False,
                        inplace = False
                        ):
        """ Preprocessing data to make it standard and comparable
        """
        columns = [self.prompt_col, self.asr_col]
        df = self.data[columns].copy()
        if asr_string_recomposition:
            # if data is string-ed list of dict, get list of dict
            logging.info("Recomposing ASR string from dict")
            df = df.applymap(lambda x: ast.literal_eval(x))
            df = df.applymap(lambda x: " ".join([e['text'] for e in x]))
        if lowercase:
            # convert text to lowercase
            logging.info("Converting df to lowercase")
            df = df.applymap(lambda x: str(x).lower())
        if convert_num2words:
            logging.info("Converting numbers to words")
            def converter(s):
                if len(s) == 4:
                    return re.sub('\d+', lambda y: num2words(y.group(), to='year'), s)
                return re.sub('\d+', lambda y: num2words(y.group()), s)
            df = df.applymap(converter)
        if punctuation_free:
            # remove punctuation
            logging.info("Removing punctuation")
            t = str.maketrans(string.punctuation, ' ' * len(string.punctuation))
            def remove_punctuation(s, translater):
                s = s.translate(translater)
                return " ".join(s.split())
            df.applymap(lambda x: remove_punctuation(x, t))
        df.fillna(" ", inplace = True)

        if not inplace:
            return df
        else:
            self.data[columns] = df

    def compute_differ_list(self, col_1, col_2, inplace = False):
        """ apply _compare_text to two self.df columns 
        and creates a new column in df for the number of common words
        """
        logging.info("Comparing %s to %s", col_1, col_2)
        if not (isinstance(col_1, str) and isinstance(col_2, str)):
            raise TypeError("col_1 and col_2 should be strings from data columns headers")
        temp = self.data.apply(lambda x: compare_text(x[col_1], x[col_2]), axis=1)

        if not inplace:
            return pd.Series(temp, name = 'differ_list')
        else:
            self.data['differ_list'] = temp

    def compute_features(self, inplace = False):
        """ compute differ list with difflib, then count words and add feautres for wcpm estimation
        """
        diff_list = self.compute_differ_list(col_1 = self.prompt_col,
                                            col_2 = self.asr_col,
                                            inplace = False
                                            )
        logging.info("Calculating features")
        temp = diff_list.apply(lambda x: get_errors_dict(x))
        temp = pd.DataFrame(temp.to_list(), columns = ["correct_words",
                                                        "added_words",
                                                        "removed_words",
                                                        "replaced_words",
                                                        "errors_dict"
                                                    ])
        temp.drop(columns = ['errors_dict'], inplace = True)
        temp['asr_word_count'] = self.data[self.asr_col].apply(lambda x: len(x.split()))
        temp['prompt_avg_word_length'] = self.data[self.prompt_col].apply(lambda x: avg_length_of_words(x))
        temp['asr_avg_word_length'] = self.data[self.asr_col].apply(lambda x: avg_length_of_words(x))
        # temp['human_wc'] = self.data['human_wcpm'].mul(self.data['scored_duration'] / 60, fill_value = 0)
        self.features = temp
        if not inplace:
            return self.features


if __name__ == "__main__":
    df = pd.read_csv("./data/wcpm_w_dur.csv")
    d = Dataset(df.drop(columns = 'human_transcript').loc[:20])
    d.preprocess_data(inplace = True)
    d.compute_features(inplace = True)
    print(d.features.head())