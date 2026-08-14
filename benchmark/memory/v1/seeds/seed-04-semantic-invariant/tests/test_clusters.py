from rook_seed.clusters import cluster_labels


def test_cluster_labels_match_expected_numbers() -> None:
    assert cluster_labels() == [0, 0, 1, 1]


def test_cluster_labels_separate_the_two_groups() -> None:
    labels = cluster_labels()
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]
