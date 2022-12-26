from ..methods.base import *
from ..methods.base import BASE_SLIP
from ..losses.clip_loss import clip_loss
from ..losses.simclr_loss import NT_Xent


class SLIP_SIMCLR(BASE_SLIP):

    def __init__(self, args):
        super().__init__(args)

        # Build Models
        self._build_model(self.hparams.img_backbone, self.hparams.text_backbone, 
                    self.hparams.projection_dim, self.hparams.dropout)


    def _build_model(self, img_backbone, text_backbone, projection_dim, dropout):
        self.img_backbone = self.img_backbones[img_backbone]
        self.text_backbone = bert_model(self.hparams.text_backbone, self.hparams.pool)
        
        # clip projector
        self.img_projector = ProjectionHeadCLIP(self.hparams.img_embedding_dim, 
                        self.hparams.projection_dim, self.hparams.dropout)
        self.text_projector = ProjectionHeadCLIP(self.hparams.text_embedding_dim, 
                        self.hparams.projection_dim, self.hparams.dropout)
        self.tokenizer = AutoTokenizer.from_pretrained(self.hparams.text_backbone, use_fast=True)
        
        # simclr objective
        self.criterion = NT_Xent(self.hparams.batch_size, self.hparams.temperature, \
                        self.hparams.gpus * self.hparams.num_nodes)

        # simclr projector
        self.simclr_projector = nn.Sequential(
            nn.Linear(self.hparams.img_embedding_dim, self.hparams.simclr_proj_hidden_dim, bias=False),
            nn.BatchNorm1d(self.hparams.simclr_proj_hidden_dim),
            nn.ReLU(inplace=True),
            nn.Linear(self.hparams.simclr_proj_hidden_dim, self.hparams.simclr_proj_output_dim, bias=True),
        )


    def shared_forward(self, batch, batch_idx, mode="train"):
        images_clip, images_ssl1, images_ssl2, text_encodings = batch
        # only use first image for clip
        images_clip, images_ssl1, images_ssl2 = torch.stack((images_clip)), torch.stack((images_ssl1)), torch.stack((images_ssl2))

        # clip
        # get embeddings
        image_features, text_features = self.img_backbone(images_clip), self.text_backbone(text_encodings)
        image_embeddings, text_embeddings = self.img_projector(image_features), self.text_projector(text_features)
        image_embeddings, text_embeddings = all_gather(image_embeddings), all_gather(text_embeddings)
        # compute loss
        c_loss = clip_loss(image_embeddings, text_embeddings, self.hparams.temperature).mean()

        # simclr 
        feat1, feat2 = self.img_backbone(images_ssl1), self.img_backbone(images_ssl2)
        z1, z2 = self.simclr_projector(feat1), self.simclr_projector(feat2)
        ssl_loss = self.criterion(z1, z2)

        # slip final loss
        loss = c_loss + self.hparams.ssl_scale * ssl_loss
        return {"loss": loss, "clip_loss": c_loss, "ssl_loss": ssl_loss}


    def training_step(self, batch, batch_idx):
        shared_out = self.shared_forward(batch, batch_idx, "train")
        loss, clip_loss, ssl_loss = shared_out["loss"], shared_out["clip_loss"], shared_out["ssl_loss"]
        self.log("train_loss", loss, on_epoch=False, on_step=True, prog_bar=True)
        self.log("train_clip_loss", clip_loss, on_epoch=False, on_step=True, prog_bar=True)
        self.log("train_ssl_loss", ssl_loss, on_epoch=False, on_step=True, prog_bar=True)
        return loss


    def validation_step(self, batch, batch_idx):
        shared_out = self.shared_forward(batch, batch_idx, "val")
        loss, clip_loss, ssl_loss = shared_out["loss"], shared_out["clip_loss"], shared_out["ssl_loss"]
        self.log("val_loss", loss, on_epoch=True, on_step=False, prog_bar=True)
        self.log("val_clip_loss", clip_loss, on_epoch=False, on_step=True, prog_bar=True)
        self.log("val_ssl_loss", ssl_loss, on_epoch=False, on_step=True, prog_bar=True)


    @property
    def learnable_params(self):
        return [
            {"type": "backbone", "params": self.img_backbone.parameters()},
            {"type": "backbone", "params": self.text_backbone.parameters()},
            {"type": "projector", "params": self.img_projector.parameters()},
            {"type": "projector", "params": self.text_projector.parameters()},
            {"type": "projector", "params": self.simclr_projector.parameters()},
        ]


    @staticmethod
    def add_model_specific_args(parent_parser):
        parser = parent_parser.add_argument_group("slip_simclr")

        # clip projector
        parser.add_argument("--img_embedding_dim", type=int, default=2048)
        parser.add_argument("--text_embedding_dim", type=int, default=768)
        parser.add_argument("--projection_dim", type=int, default=512)
        parser.add_argument("--dropout", type=int, default=0.1)
        parser.add_argument("--temperature", type=float, default=0.1)

        # simclr projector
        parser.add_argument("--simclr_proj_hidden_dim", type=int, default=512)
        parser.add_argument("--simclr_proj_output_dim", type=int, default=128)
        parser.add_argument("--ssl_scale", type=float, default=1.0)

        return parent_parser


