import argparse
import os.path

# import plotter
from torch.utils.tensorboard import SummaryWriter  # for vis


def main(args, tb):
    import json, time, os, sys, glob
    import shutil
    import warnings
    import numpy as np
    import torch
    from torch import optim
    from torch.utils.data import DataLoader
    import queue
    import copy
    import torch.nn as nn
    import torch.nn.functional as F

    import random
    import os.path
    import subprocess
    from concurrent.futures import ProcessPoolExecutor
    from utils import (
        worker_init_fn,
        get_pdbs,
        loader_pdb,
        build_training_clusters,
        build_cv_clusters,
        PDB_dataset,
        StructureDataset,
        StructureLoader,
    )
    from model_utils import (
        featurize,
        loss_smoothed,
        loss_nll,
        get_std_opt,
        ProteinMPNN,
        ProteinMPNN_FixedRFP,
    )

    scaler = torch.cuda.amp.GradScaler()

    device = torch.device("cuda:0" if (torch.cuda.is_available()) else "cpu")

    base_folder = time.strftime(args.path_for_outputs, time.localtime())

    # Create the folder for the outputs
    if base_folder[-1] != "/":
        base_folder += "/"
    if not os.path.exists(base_folder):
        os.makedirs(base_folder)
    subfolders = ["model_weights", "parameter_logs"]
    for subfolder in subfolders:
        if not os.path.exists(base_folder + subfolder):
            os.makedirs(base_folder + subfolder)

    PATH = args.previous_checkpoint

    logfile = base_folder + "log.txt"  # for plotting

    if not PATH:
        with open(logfile, "w") as f:
            f.write("Epoch\tTrain\tValidation\n")

    # Structuring the dataset
    data_path = args.path_for_training_data
    params = {
        "LIST": f"{data_path}/list.csv",
        # "VAL": f"{data_path}/valid_clusters.txt",
        "TEST": f"{data_path}/test_clusters.txt",
        "DIR": f"{data_path}",
        "DATCUT": "2030-Jan-01",
        "RESCUT": args.rescut,  # resolution cutoff for PDBs
        "HOMO": 0.70,  # min seq.id. to detect homo chains
    }

    LOAD_PARAM = {
        "batch_size": 1,
        "shuffle": True,
        "pin_memory": False,
        "num_workers": 4,
    }

    if args.fixed_network_transfer_learning == "True":
        args.fixed_network_transfer_learning = True
    else:
        args.fixed_network_transfer_learning = False

    if args.debug:
        args.num_examples_per_epoch = 50
        args.max_protein_length = 1000
        args.batch_size = 1000

    # implementing Monte Carlo CV; we need highly biased, but low variance model to get good results
    # https://towardsdatascience.com/cross-validation-k-fold-vs-monte-carlo-e54df2fc179b
    train_original, test = build_training_clusters(params, args.debug)

    # train_set = PDB_dataset(list(train.keys()), loader_pdb, train, params)
    # train_loader = torch.utils.data.DataLoader(
    #     train_set, worker_init_fn=worker_init_fn, **LOAD_PARAM
    # )
    # valid_set = PDB_dataset(list(valid.keys()), loader_pdb, valid, params)
    # valid_loader = torch.utils.data.DataLoader(
    #     valid_set, worker_init_fn=worker_init_fn, **LOAD_PARAM
    # )

    # test
    test_set = PDB_dataset(list(test.keys()), loader_pdb, test, params)
    test_loader = torch.utils.data.DataLoader(
        test_set, worker_init_fn=worker_init_fn, **LOAD_PARAM
    )

    model = ProteinMPNN(
        node_features=args.hidden_dim,
        edge_features=args.hidden_dim,
        hidden_dim=args.hidden_dim,
        num_encoder_layers=args.num_encoder_layers,
        num_decoder_layers=args.num_encoder_layers,
        k_neighbors=args.num_neighbors,
        dropout=args.dropout,
        augment_eps=args.backbone_noise,
    )
    model.to(device)

    if args.fixed_network_transfer_learning:
        model_final_classifier = ProteinMPNN_FixedRFP(
            hidden_dim=args.hidden_dim,
            dropout=args.dropout,
        )

    if PATH:
        # Transfer learning from a previous checkpoint
        checkpoint = torch.load(PATH)

        # error
        total_step = 0
        epoch = 0

        # total_step = checkpoint["step"] if checkpoint["step"] is not None else 0 # write total_step from the checkpoint
        # epoch = checkpoint["epoch"] if checkpoint["step"] else 0  # write epoch from the checkpoint
        model.load_state_dict(checkpoint["model_state_dict"])

        # fixed network TL
        if args.fixed_network_transfer_learning:
            for param in model.parameters():
                param.requires_grad = False

        trasnfer_epoch = 0
    else:
        total_step = 0
        epoch = 0

    if args.fixed_network_transfer_learning:
        optimizer = get_std_opt(
            model_final_classifier.parameters, args.hidden_dim, total_step
        )
    else:
        optimizer = get_std_opt(model.parameters(), args.hidden_dim, total_step)

    if PATH:
        # DEV
        # error None
        # optimizer.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        pass

    # train_perplexity_history = []
    # validation_perplexity_history = []
    # train_accuracy_history = []
    # validation_accuracy_history = []

    with ProcessPoolExecutor(max_workers=12) as executor:
        for e in range(args.num_epochs):
            # for MC CV
            train, valid = build_cv_clusters(train_original, args.debug)

            train_set = PDB_dataset(list(train.keys()), loader_pdb, train, params)
            train_loader = torch.utils.data.DataLoader(
                train_set, worker_init_fn=worker_init_fn, **LOAD_PARAM
            )
            valid_set = PDB_dataset(list(valid.keys()), loader_pdb, valid, params)
            valid_loader = torch.utils.data.DataLoader(
                valid_set, worker_init_fn=worker_init_fn, **LOAD_PARAM
            )

            q = queue.Queue(maxsize=3)
            p = queue.Queue(maxsize=3)
            for i in range(3):
                q.put_nowait(
                    executor.submit(
                        get_pdbs,
                        train_loader,
                        1,
                        args.max_protein_length,
                        args.num_examples_per_epoch,
                    )
                )
                p.put_nowait(
                    executor.submit(
                        get_pdbs,
                        valid_loader,
                        1,
                        args.max_protein_length,
                        args.num_examples_per_epoch,
                    )
                )
            pdb_dict_train = q.get().result()
            pdb_dict_valid = p.get().result()

            dataset_train = StructureDataset(
                pdb_dict_train, truncate=None, max_length=args.max_protein_length
            )
            dataset_valid = StructureDataset(
                pdb_dict_valid, truncate=None, max_length=args.max_protein_length
            )

            loader_train = StructureLoader(dataset_train, batch_size=args.batch_size)
            loader_valid = StructureLoader(dataset_valid, batch_size=args.batch_size)

            reload_c = 0

            t0 = time.time()
            e = epoch + e
            if args.fixed_network_transfer_learning:
                model_final_classifier.train()
            else:
                model.train()
            train_sum, train_weights = 0.0, 0.0
            train_acc = 0.0

            for _, batch in enumerate(loader_train):
                start_batch = time.time()
                (
                    X,
                    S,
                    mask,
                    lengths,
                    chain_M,
                    residue_idx,
                    mask_self,
                    chain_encoding_all,
                ) = featurize(batch, device)
                elapsed_featurize = time.time() - start_batch
                optimizer.zero_grad()
                mask_for_loss = mask * chain_M

                if args.mixed_precision:
                    with torch.cuda.amp.autocast():
                        log_probs = model(
                            X, S, mask, chain_M, residue_idx, chain_encoding_all
                        )
                        if args.fixed_network_transfer_learning:
                            log_probs = model_final_classifier(log_probs)

                        _, loss_av_smoothed = loss_smoothed(S, log_probs, mask_for_loss)

                    scaler.scale(loss_av_smoothed).backward()

                    if args.gradient_norm > 0.0:
                        if args.fixed_network_transfer_learning:
                            total_norm = torch.nn.utils.clip_grad_norm_(
                                model_final_classifier.parameters(), args.gradient_norm
                            )
                        else:
                            total_norm = torch.nn.utils.clip_grad_norm_(
                                model.parameters(), args.gradient_norm
                            )

                    scaler.step(optimizer)
                    scaler.update()
                else:
                    log_probs = model(
                        X, S, mask, chain_M, residue_idx, chain_encoding_all
                    )
                    if args.fixed_network_transfer_learning:
                        log_probs = model_final_classifier(log_probs)

                    _, loss_av_smoothed = loss_smoothed(S, log_probs, mask_for_loss)
                    loss_av_smoothed.backward()

                    if args.gradient_norm > 0.0:
                        if args.fixed_network_transfer_learning:
                            total_norm = torch.nn.utils.clip_grad_norm_(
                                model_final_classifier.parameters(), args.gradient_norm
                            )
                        else:
                            total_norm = torch.nn.utils.clip_grad_norm_(
                                model.parameters(), args.gradient_norm
                            )

                    optimizer.step()

                loss, loss_av, true_false = loss_nll(S, log_probs, mask_for_loss)

                train_sum += torch.sum(loss * mask_for_loss).cpu().data.numpy()
                train_acc += torch.sum(true_false * mask_for_loss).cpu().data.numpy()
                train_weights += torch.sum(mask_for_loss).cpu().data.numpy()

                total_step += 1

            if args.fixed_network_transfer_learning:
                model_final_classifier.eval()
            else:
                model.eval()
            with torch.no_grad():
                validation_sum, validation_weights = 0.0, 0.0
                validation_acc = 0.0
                for _, batch in enumerate(loader_valid):
                    (
                        X,
                        S,
                        mask,
                        lengths,
                        chain_M,
                        residue_idx,
                        mask_self,
                        chain_encoding_all,
                    ) = featurize(batch, device)
                    log_probs = model(
                        X, S, mask, chain_M, residue_idx, chain_encoding_all
                    )
                    if args.fixed_network_transfer_learning:
                        log_probs = model_final_classifier(log_probs)
                    mask_for_loss = mask * chain_M
                    loss, loss_av, true_false = loss_nll(S, log_probs, mask_for_loss)

                    validation_sum += torch.sum(loss * mask_for_loss).cpu().data.numpy()
                    validation_acc += (
                        torch.sum(true_false * mask_for_loss).cpu().data.numpy()
                    )
                    validation_weights += torch.sum(mask_for_loss).cpu().data.numpy()

            train_loss = train_sum / train_weights
            train_accuracy = train_acc / train_weights
            train_perplexity = np.exp(train_loss)
            validation_loss = validation_sum / validation_weights
            validation_accuracy = validation_acc / validation_weights
            validation_perplexity = np.exp(validation_loss)

            train_perplexity_ = np.format_float_positional(
                np.float32(train_perplexity), unique=False, precision=3
            )
            validation_perplexity_ = np.format_float_positional(
                np.float32(validation_perplexity), unique=False, precision=3
            )
            train_accuracy_ = np.format_float_positional(
                np.float32(train_accuracy), unique=False, precision=3
            )
            validation_accuracy_ = np.format_float_positional(
                np.float32(validation_accuracy), unique=False, precision=3
            )

            t1 = time.time()
            dt = np.format_float_positional(
                np.float32(t1 - t0), unique=False, precision=1
            )
            with open(logfile, "a") as f:
                f.write(
                    f"epoch: {e+1}, step: {total_step}, time: {dt}, train: {train_perplexity_}, valid: {validation_perplexity_}, train_acc: {train_accuracy_}, valid_acc: {validation_accuracy_}\n"
                )
            print(
                f"epoch: {e+1}, step: {total_step}, time: {dt}, train: {train_perplexity_}, valid: {validation_perplexity_}, train_acc: {train_accuracy_}, valid_acc: {validation_accuracy_}"
            )

            # for plotting the history
            # train_perplexity_history.append(train_perplexity_)
            # validation_perplexity_history.append(validation_perplexity_)
            # train_accuracy_history.append(train_accuracy_)
            # validation_accuracy_history.append(validation_accuracy_)

            checkpoint_filename_last = (
                base_folder + "model_weights/epoch_last.pt".format(e + 1, total_step)
            )
            torch.save(
                {
                    "epoch": e + 1,
                    "step": total_step,
                    "num_edges": args.num_neighbors,
                    "noise_level": args.backbone_noise,
                    "model_state_dict": model.state_dict(),
                    "optimizer_state_dict": optimizer.optimizer.state_dict(),
                },
                checkpoint_filename_last,
            )

            if (e + 1) % args.save_model_every_n_epochs == 0:
                checkpoint_filename = (
                    base_folder
                    + "model_weights/epoch{}_step{}.pt".format(e + 1, total_step)
                )
                torch.save(
                    {
                        "epoch": e + 1,
                        "step": total_step,
                        "num_edges": args.num_neighbors,
                        "noise_level": args.backbone_noise,
                        "model_state_dict": model.state_dict(),
                        "optimizer_state_dict": optimizer.optimizer.state_dict(),
                    },
                    checkpoint_filename,
                )

            # VISUALIZATION OF THE IMPORTATNT TRAINABLE VARIABLES
            if args.fixed_network_transfer_learning:

                tb.add_histogram(
                    "final_log_prob_out.weight",
                    model_final_classifier.W_out.weight,
                    e + 1,
                )
                tb.add_histogram(
                    "final_log_prob_out.bias", model_final_classifier.W_out.bias, e + 1
                )

            else:
                # Enc
                tb.add_histogram(
                    "last_enc_positionalFF_out.weight",
                    model.encoder_layers[-1].dense.W_out.weight,
                    e + 1,
                )
                tb.add_histogram(
                    "last_enc_positionalFF_out.bias",
                    model.encoder_layers[-1].dense.W_out.bias,
                    e + 1,
                )
                # Dec
                tb.add_histogram(
                    "last_dec_positionalFF_out.weight",
                    model.decoder_layers[-1].dense.W_out.weight,
                    e + 1,
                )
                tb.add_histogram(
                    "last_dec_positionalFF_out.bias",
                    model.decoder_layers[-1].dense.W_out.bias,
                    e + 1,
                )

        # train and validation visualization
        # => jupyter notebook
    # testing
    # inference on testing dataset


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    argparser.add_argument(
        "--path_for_training_data",
        type=str,
        default="my_path/pdb_2021aug02",
        help="path for loading training data",
    )
    argparser.add_argument(
        "--path_for_outputs",
        type=str,
        default="./tl",
        help="path for logs and model weights",
    )
    argparser.add_argument(
        "--previous_checkpoint",
        type=str,
        default="",
        help="path for previous model weights, e.g. file.pt",
    )
    argparser.add_argument(
        "--num_epochs", type=int, default=200, help="number of epochs to train for"
    )
    argparser.add_argument(
        "--save_model_every_n_epochs",
        type=int,
        default=10,
        help="save model weights every n epochs",
    )
    argparser.add_argument(
        "--reload_data_every_n_epochs",
        type=int,
        default=2,
        help="reload training data every n epochs",
    )
    argparser.add_argument(
        "--num_examples_per_epoch",
        type=int,
        default=1000000,
        help="number of training example to load for one epoch",
    )
    argparser.add_argument(
        "--batch_size", type=int, default=10000, help="number of tokens for one batch"
    )
    argparser.add_argument(
        "--max_protein_length",
        type=int,
        default=10000,
        help="maximum length of the protein complext",
    )
    argparser.add_argument(
        "--hidden_dim", type=int, default=128, help="hidden model dimension"
    )
    argparser.add_argument(
        "--num_encoder_layers", type=int, default=3, help="number of encoder layers"
    )
    argparser.add_argument(
        "--num_decoder_layers", type=int, default=3, help="number of decoder layers"
    )
    argparser.add_argument(
        "--num_neighbors",
        type=int,
        default=48,
        help="number of neighbors for the sparse graph",
    )
    argparser.add_argument(
        "--dropout", type=float, default=0.1, help="dropout level; 0.0 means no dropout"
    )
    argparser.add_argument(
        "--backbone_noise",
        type=float,
        default=0.2,
        help="amount of noise added to backbone during training",
    )
    argparser.add_argument(
        "--rescut", type=float, default=3.5, help="PDB resolution cutoff"
    )
    argparser.add_argument(
        "--debug", type=bool, default=False, help="minimal data loading for debugging"
    )
    argparser.add_argument(
        "--gradient_norm",
        type=float,
        default=-1.0,
        help="clip gradient norm, set to negative to omit clipping",
    )
    argparser.add_argument(
        "--mixed_precision", type=bool, default=True, help="train with mixed precision"
    )
    # argparser.add_argument(
    #     "--num_cross_validation", type=int, default=5, help="Monte Carlo CV counts"
    # ) # for every epoch
    argparser.add_argument(
        "--fixed_network_transfer_learning",
        type=bool,
        default=False,
        help="train with fixed network weights",
    )

    args = argparser.parse_args()

    tb = SummaryWriter()
    main(args, tb)
    tb.close()
