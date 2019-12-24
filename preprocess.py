"""
Graph preprocessing file.
Collects features and assembles the population graph.


Collects the relevant timeseries, 
computes functional/structural connectivity matrices
computes graph adjacency scores
connects nodes into a graph, assigning collected features
"""

import numpy as np
import pandas as pd
import os

import torch
from torch_geometric.data import Data

import sklearn
from sklearn.preprocessing import OneHotEncoder
from sklearn.model_selection import StratifiedShuffleSplit

import precompute

# Data sources.
data_root = 'data'
data_timeseries = 'data/raw_ts'
data_precomputed_fcms = 'data/processed_ts'
data_phenotype = 'data/phenotype.csv'
graph_root = 'data/graph'

# Graph construction phenotypic parameters.
# http://biobank.ndph.ox.ac.uk/showcase/field.cgi?id=31
SEX_UID = '31-0.0'
# http://biobank.ndph.ox.ac.uk/showcase/field.cgi?id=21003
AGE_UID = '21003-2.0'


def get_subject_ids(num_subjects=None, randomise=True, seed=0):
    """
    Gets the list of subject IDs for a spcecified number of subjects. If the number of subjects is not specified, all
    IDs are returned.
  
    Args:
        num_subjects: The number of subjects.
        randomise: Indicates whether to use a random seed for selection of subjects.
        seed: Seed value.

    Returns:
        List of subject IDs.
    """
    subject_ids = np.load(os.path.join(data_root, 'subject_ids.npy'))

    if not num_subjects:
        return subject_ids

    if randomise:
        return np.random.choice(subject_ids, num_subjects, replace=False)
    else:
        return subject_ids[:num_subjects]


# TODO: include the argument for the kind of connectivity matrix (partial correlation, correlation, lasso,...)
def get_functional_connectivity(subject_id):
    """
    Returns the correlation matrix for the parcellated timeseries data, precomputing if necessary.

    Args:
        subject_id: ID of subject.

    Returns:
        The flattened lower triangle of the correlation matrix for the parcellated timeseries data.
    """
    if subject_id + '.npy' not in os.listdir(data_precomputed_fcms):
        precompute.precompute_fcm(subject_id)

    return np.load(os.path.join(data_precomputed_fcms, subject_id + '.npy'))


def get_all_functional_connectivities(subject_ids):
    connectivities = []
    for i, subject_id in enumerate(subject_ids):
        connectivity = get_functional_connectivity(subject_id)
        assert len(connectivity) == 70500
        connectivities.append(connectivity)

    return connectivities


def functional_connectivities_pca(connectivities, train_idx, random_state=0):
    connectivity_pca = sklearn.decomposition.PCA(random_state=random_state)
    connectivity_pca.fit(connectivities[train_idx])
    return connectivity_pca.transform(connectivities)


def get_similarity(phenotypes, subject_i, subject_j):
    """
    Computes the similarity score between two subjects.

    Args:
        phenotypes: Dataframe with phenotype values.
        subject_i: First subject.
        subject_j: Second subject.

    Returns:
        Similarity score.
    """
    return int(phenotypes.loc[subject_i, SEX_UID] == phenotypes.loc[subject_j, SEX_UID])


def construct_edge_list(phenotypes, similarity_function=get_similarity, similarity_threshold=0.5):
    """
    Constructs the adjacency list of the population graph based on a similarity metric provided.
  
    Args:
        phenotypes: Dataframe with phenotype values.
        similarity_function: Function which is returns similarity between two subjects according to some metric.
        similarity_threshold: The threshold above which the edge should be added.

    Returns:
        Graph connectivity in coordinate format of shape [2, num_edges]. The
        same edge (v, w) appears twice as (v, w) and (w, v) to represent
        bidirectionality.
    """
    v_list = []
    w_list = []

    for i, id_i in enumerate(phenotypes.index):
        iter_j = iter(enumerate(phenotypes.index))
        [next(iter_j) for _ in range(i + 1)]
        for j, id_j in iter_j:
            if similarity_function(phenotypes, id_i, id_j) > similarity_threshold:
                v_list.extend([i, j])
                w_list.extend([j, i])

    return [v_list, w_list]


def get_random_subject_split(num_subjects, test=0.1, seed=0):
    np.random.seed(seed)

    num_train = int(num_subjects * 0.85)
    num_validate = int(num_subjects * 0.05)

    train_val_idx = np.random.choice(range(num_subjects), num_train + num_validate, replace=False)
    train_idx = np.random.choice(train_val_idx, num_train, replace=False)
    validate_idx = list(set(train_val_idx) - set(train_idx))
    test_idx = list(set(range(num_subjects)) - set(train_val_idx))

    assert (len(np.intersect1d(train_idx, validate_idx)) == 0)
    assert (len(np.intersect1d(train_idx, test_idx)) == 0)
    assert (len(np.intersect1d(validate_idx, test_idx)) == 0)

    return train_idx, validate_idx, test_idx


def get_stratified_subject_split(features, labels, test_size=None, random_state=None):
    train_test_split = StratifiedShuffleSplit(n_splits=1, test_size=test_size, random_state=random_state)

    for train_validate_index, test_index in train_test_split.split(features, labels):
        features_train = features[train_validate_index]
        labels_train = labels[train_validate_index]

        train_validate_index = np.array(train_validate_index)
        test_index = np.array(test_index)

        train_validate_split = StratifiedShuffleSplit(n_splits=1, test_size=0.1, random_state=random_state)
        for train_index, validate_index in train_validate_split.split(features_train, labels_train):
            return train_validate_index[train_index], train_validate_index[validate_index], test_index


def get_subject_split_masks(train_index, validate_index, test_index):
    num_subjects = len(train_index) + len(validate_index) + len(test_index)

    train_mask = np.zeros(num_subjects, dtype=bool)
    train_mask[train_index] = True

    validate_mask = np.zeros(num_subjects, dtype=bool)
    validate_mask[validate_index] = True

    test_mask = np.zeros(num_subjects, dtype=bool)
    test_mask[test_index] = True

    return train_mask, validate_mask, test_mask


def construct_population_graph(size=None,
                               functional=False,
                               pca=False,
                               structural=True,
                               euler=True,
                               save=True,
                               save_dir=graph_root,
                               name=None):
    if name is None:
        name = 'population_graph_' \
               + (str(size) if size is not None else 'all') \
               + ('_functional' if functional else '') \
               + ('_PCA' if functional and pca else '') \
               + ('_structural' if structural else '') \
               + ('_euler' if euler else '') \
               + '.pt'

    subject_ids = get_subject_ids(size)

    # Collect the required data.
    phenotypes = precompute.extract_phenotypes([SEX_UID, AGE_UID], subject_ids)
    assert len(np.intersect1d(subject_ids, phenotypes.index)) == 0

    if functional:
        functional_data = get_all_functional_connectivities(subject_ids)
    else:
        functional_data = pd.DataFrame()

    if structural:
        structural_data = precompute.extract_cortical_thickness(subject_ids)
        assert len(np.intersect1d(subject_ids, structural_data.index)) == 0
    else:
        structural_data = pd.DataFrame()

    if euler:
        euler_data = precompute.extract_euler(subject_ids)
        assert len(np.intersect1d(subject_ids, euler_data.index)) == 0
    else:
        euler_data = pd.DataFrame()

    # sex = OneHotEncoder().fit_transform(phenotypes[SEX_UID].to_numpy().reshape(-1, 1))
    # ct_sex = np.concatenate((ct.to_numpy(), sex.toarray()), axis=1)
    # if euler:
    #
    # else:
    # connectivities = ct_sex

    num_subjects = len(subject_ids)
    print('{} subjects remaining for graph construction.'.format(num_subjects))

    features = np.concatenate([functional_data.to_numpy(),
                               structural_data.to_numpy(),
                               euler_data.to_numpy()], axis=1)
    labels = [phenotypes[AGE_UID].iloc(subject_ids).tolist()]

    # Split subjects into train, validation and test sets.
    stratified_subject_split = get_stratified_subject_split(features, labels)
    train_mask, validate_mask, test_mask = get_subject_split_masks(*stratified_subject_split)

    # Optional functional data preprocessing (PCA) based on the traning index.
    if functional and pca:
        functional_data = functional_connectivities_pca(functional_data, train_mask)

    # Scaling structural data based on training index.
    if structural:
        structural_scaler = sklearn.preprocessing.StandardScaler()
        structural_scaler.fit(structural_data[train_mask])
        structural_data = structural_scaler.transform(structural_data)

    # Scaling Euler index data based on training index.
    if euler:
        euler_scaler = sklearn.preprocessing.StandardScaler()
        euler_scaler.fit(euler_data[train_mask])
        euler_data = euler_scaler.transform(euler_data)

    # Unify feature sets into one feature vector.
    features = np.concatenate([functional_data.to_numpy(),
                               structural_data.to_numpy(),
                               euler_data.to_numpy()], axis=1)

    feature_tensor = torch.tensor(features, dtype=torch.float32)
    label_tensor = torch.tensor(labels, dtype=torch.float32).transpose_(0, 1)

    # Construct the edge index.
    edge_index = torch.tensor(
        construct_edge_list(subject_ids),
        dtype=torch.long)

    train_mask_tensor = torch.tensor(train_mask, dtype=torch.bool)
    validate_mask_tensor = torch.tensor(validate_mask, dtype=torch.bool)
    test_mask_tensor = torch.tensor(test_mask, dtype=torch.bool)

    population_graph = Data(
        x=feature_tensor,
        edge_index=edge_index,
        y=label_tensor,
        train_mask=train_mask_tensor,
        test_mask=test_mask_tensor
    )

    population_graph.validate_mask = validate_mask_tensor

    if save:
        torch.save(population_graph, os.path.join(save_dir, name))

    return population_graph


def load_population_graph(graph_root, name):
    return torch.load(os.path.join(graph_root, name))


if __name__ == '__main__':
    graph = construct_population_graph(1000)