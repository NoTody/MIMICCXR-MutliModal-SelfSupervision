import argparse
import pytorch_lightning as pl
from pytorch_lightning import Trainer, seed_everything
from pytorch_lightning.callbacks import TQDMProgressBar, ModelCheckpoint
from pytorch_lightning.callbacks.early_stopping import EarlyStopping
from pytorch_lightning.strategies.ddp import DDPStrategy
from pytorch_lightning import loggers as pl_loggers
from mm_pkg.methods import METHODS
from mm_pkg.model_utils.misc_utils import make_contiguous

import sys
sys.path.append("..")

IMG_BACKBONES = {
    "resnet2d_18",
    "resnet2d_50",
    "resnet2d_101",
    "densenet2d_121",
}

TEXT_BACKBONES = {
    "microsoft/BiomedVLP-CXR-BERT-general",
    "microsoft/BiomedVLP-CXR-BERT-specialized",
}

def parse_args_pretrain():
    """Parses dataset, augmentation, pytorch lightning, model specific and additional args.
    First adds shared args such as dataset, augmentation and pytorch lightning args, then pulls the
    model name from the command and proceeds to add model specific args from the desired class. If
    wandb is enabled, it adds checkpointer args. Finally, adds additional non-user given parameters.
    Returns:
        argparse.Namespace: a namespace containing all args needed for pretraining.
    """
    parser = argparse.ArgumentParser()

    # add pytorch lightning trainer args
    #parser = pl.Trainer.add_argparse_args(parser)

    # method args
    parser.add_argument("--method", type=str)

    # model specific args
    temp_args, _ = parser.parse_known_args()
    parser = METHODS[temp_args.method].add_model_specific_args(parser)

    # pytorchlightning specific args
    parser.add_argument("--seed", type=int, default=2022)
    parser.add_argument("--num_workers", type=int, default=0)
    parser.add_argument("--gpus", type=int, default=1)
    parser.add_argument("--num_nodes", type=int, default=1)
    parser.add_argument("--precision", type=int, default=16)
    parser.add_argument("--save_dir", type=str, default="./")
    parser.add_argument("--val_interval", type=float, default=1.)
    parser.add_argument("--use_ddp", action='store_true')
    parser.add_argument('--pin_mem', default=False, action='store_true')

    # general model args
    parser.add_argument("--batch_size", type=int, default=16)
    parser.add_argument("--max_epochs", type=int, default=100)
    parser.add_argument("--features_dim", type=int, default=2048)
    parser.add_argument("--clip_grad", type=float, default=0.)
    parser.add_argument('--pretrained', default=False, action='store_true')
    #parser.add_argument('--no-pretrained', dest='pretrained', action='store_false')

    # image model
    parser.add_argument("--img_backbone", choices=IMG_BACKBONES, type=str, default="resnet2d_18")
    parser.add_argument("--two_transform", default=False, action='store_true')
    #parser.add_argument("--no-two_transform", dest='two_transform', action='store_false')

    # text model
    parser.add_argument("--text_backbone", choices=TEXT_BACKBONES, type=str, default="microsoft/BiomedVLP-CXR-BERT-general")
    parser.add_argument("--max_length", type=int, default=128)
    parser.add_argument("--pool", type=str, default="cls")
    parser.add_argument('--full_report', default=False, action='store_true')
    #parser.add_argument('--no-full_report', dest='full_report', action='store_false')

    # learning rate setup
    parser.add_argument("--lr_backbone", type=float, default=1e-4)
    parser.add_argument("--lr_projector", type=float, default=1e-4)
    #parser.add_argument("--lr_predictor", type=float, default=1e-4)
    parser.add_argument("--min_lr_backbone", type=float, default=1e-5)
    parser.add_argument("--min_lr_projector", type=float, default=1e-5)

    # weight decay setup
    parser.add_argument("--weight_decay", type=float, default=1e-4) # 1e-4
    parser.add_argument("--weight_decay_end", type=float, default=1e-5)

    # momentum setup
    parser.add_argument("--start_momentum", type=float, default=0.99)
    parser.add_argument("--end_momentum", type=float, default=1.)
    # momentum with sgd is used
    parser.add_argument("--momentum", type=float, default=0.90)

    # misc
    parser.add_argument("--per_warmup_steps", type=float, default=0.03)
    parser.add_argument("--optimizer",  choices=['adamw', 'lamb', 'sgd', 'lars'], type=str, default="adamw")

    # dataset args
    parser.add_argument("--train_df_path", type=str, default= '/gpfs/data/denizlab/Users/hh2740/mimic-cxr_half_train.csv')
    parser.add_argument("--val_df_path", type=str, default= '/gpfs/data/denizlab/Users/hh2740/mimic-cxr_half_val.csv')

    args = parser.parse_args()
    return args


def main():
    args = parse_args_pretrain()

    print(f"args Report:\n{args}")
    seed_everything(args.seed)

    model = METHODS[args.method](args)
    make_contiguous(model)

    checkpoint_callback = ModelCheckpoint(
        save_top_k=1,
        verbose=True,
        monitor='val_loss',
        mode='min',
        save_weights_only=False,
        dirpath=args.save_dir,
    )

    tb_logger = pl_loggers.TensorBoardLogger(save_dir=args.save_dir)
    early_stop_callback = EarlyStopping(monitor="val_loss", min_delta=0.00, patience=5, verbose=False, mode="min")
    bar = TQDMProgressBar(refresh_rate=5, process_position=0)

    callbacks = [checkpoint_callback, early_stop_callback, bar]

    trainer = Trainer.from_argparse_args(
        args,
        enable_checkpointing=True,
        devices=args.gpus,
        accelerator="gpu",
        precision=args.precision,
        num_nodes=args.num_nodes,
        max_epochs=args.max_epochs,
        val_check_interval=args.val_interval,
        logger=tb_logger,
        callbacks=callbacks,
        strategy="ddp",
        sync_batchnorm=True
        #strategy=DDPStrategy(find_unused_parameters=True)
        #if args.use_ddp
        #else None,
    )

    trainer.fit(model)


if __name__ == "__main__":
    main()

