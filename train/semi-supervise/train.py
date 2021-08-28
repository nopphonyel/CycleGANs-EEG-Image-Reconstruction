import torch
import sys
import time
import datetime
import math

from dataset.mind_big_data import MindBigData
from model.semi_supervised.loss_func import *
from torch.utils.data import DataLoader

from config import *

from torchvision.utils import make_grid, save_image

# Dataset initialization
dataset = MindBigData(dev=DEV)
dat_loader = DataLoader(dataset=dataset, batch_size=BS, shuffle=True)

# Model initialization
input_sample = next(iter(dat_loader))[0]

NUM_LIM_CLASS = 40

sx = SemanticImageExtractor(output_class_num=NUM_LIM_CLASS,
                            feature_size=feature_size).to(DEV)
# Argument expected_shape : send some sample data to let model determine its structure
sy = SemanticEEGExtractor(expected_shape=input_sample,
                          output_class_num=NUM_LIM_CLASS,
                          feature_size=feature_size).to(DEV)
d1 = D1().to(DEV)
d2 = D2().to(DEV)
G = Generator().to(DEV)

# Optimizer initialization
sx_op = torch.optim.Adam(sx.parameters(), lr=mu1)
sy_op = torch.optim.Adam(sy.parameters(), lr=mu1)

d1_op = torch.optim.Adam(d1.parameters(), lr=mu2)
d2_op = torch.optim.Adam(d2.parameters(), lr=mu2)
G_op = torch.optim.Adam(G.parameters(), lr=mu2)


def load_model(start_epch):
    if start_epch != 0:
        # Load pretrained models
        print("<I> : Loading model at epoch check point = %d" % start_epch)
        if LOAD_FE:
            sx.load_state_dict(torch.load("saved_models/%s/%d_sx.pth" % (dataset.get_name(), start_epch)))
            sy.load_state_dict(torch.load("saved_models/%s/%d_sy.pth" % (dataset.get_name(), start_epch)))
        if LOAD_GEN:
            G.load_state_dict(torch.load("saved_models/%s/%d_G.pth" % (dataset.get_name(), start_epch)))
        if LOAD_DIS:
            d1.load_state_dict(torch.load("saved_models/%s/%d_d1.pth" % (dataset.get_name(), start_epch)))
            d2.load_state_dict(torch.load("saved_models/%s/%d_d2.pth" % (dataset.get_name(), start_epch)))


# Some visualization function
def sample_images(epch):
    """Saves a generated sample from the test set"""
    real_eeg, real_label, real_stim = next(iter(dat_loader))
    sy.eval()
    G.eval()
    eeg_features, p_label = sy(real_eeg)
    curr_BS = real_eeg.shape[0]

    eeg_features = eeg_features.squeeze(1).detach()
    p_label = p_label.squeeze(1).detach()
    fake_stim = G(z=torch.rand(curr_BS, Generator.EXPECTED_NOISE).to(DEV), semantic=eeg_features, label=p_label)
    fake_stim = make_grid(fake_stim, nrow=5, normalize=True)
    # Arange images along y-axis
    real_stim = make_grid(real_stim, nrow=5, normalize=True)
    image_grid = torch.cat((real_stim, fake_stim), 1)
    save_image(image_grid, "images/%s/%s_reduce.png" % (dataset.get_name(), epch), normalize=False)


def check_nan(chck, **log):
    if math.isnan(chck):
        print("<!> : NAN detected --------")
        for each_elm in log:
            print(each_elm, log[each_elm], sep='=')
        print("---------------------------")


# X stands for image
# Y stands for EEG

prev_time = time.time()
load_model(EPCH_START)
for epch in range(EPCH_START, EPCH_END+1):
    for i, (y_p, l_real_p, x_p) in enumerate(dat_loader):
        # SEMANTIC NETWORK TRAINING SECTION
        # print("UPDATING SEMANTIC EXTRACTOR")
        sx_op.zero_grad()
        sy_op.zero_grad()
        x_u = x_p
        # For this dataset, I think its still make sense to do this
        l_real_p = l_real_p.unsqueeze(1)
        l_real_u = l_real_p

        fx_p, lx_p = sx(x_p)
        fy_p, ly_p = sy(y_p)

        fx_u = fx_p  # For this dataset, I think its still make sense to do this
        lx_u = lx_p

        j1 = j1_loss(l=l_real_p, fx=fx_p, fy=fy_p)
        j2 = j2_loss(l_real=l_real_p, lx=lx_p)
        j3 = j3_loss(l_real=l_real_p, ly=ly_p)
        j4 = j4_loss(fy_p=fy_p, l_p=l_real_p, fx_u=fx_u, l_u=l_real_u)
        j5 = j5_loss(l_real_u=l_real_u, lx_u=lx_u)
        j_loss = (alp0 * j1) + (alp1 * j2) + (alp2 * j3) + (alp0 * j4) + (alp3 * j5)
        # j_loss = j1 + (alp1 * j2) + (alp2 * j3)
        # check_nan(j_loss.item(), j1=j1.item(), j2=j2.item(), j3=j3.item(), j4=j4.item(), j5=j5.item())
        j_loss.backward()
        sx_op.step()
        sy_op.step()

        # DISCRIMINATOR TRAINING SECTION
        # print("UPDATING DISCRIM")
        d1_op.zero_grad()
        d2_op.zero_grad()

        # Reshape the tensor corresponding to the generator and detach everything
        fy_p = fy_p.squeeze(1).detach()
        ly_p = ly_p.squeeze(1).detach()
        fx_u = fx_u.squeeze(1).detach()
        lx_u = lx_u.squeeze(1).detach()

        # Since some of batch might get reduce due to end of iteration
        curr_BS = y_p.shape[0]

        x_p_gen = G.forward(z=torch.rand(curr_BS, Generator.EXPECTED_NOISE).to(DEV), semantic=fy_p, label=ly_p)
        x_p_gen_dtch = x_p_gen.detach()
        x_u_gen = G.forward(z=torch.rand(curr_BS, Generator.EXPECTED_NOISE).to(DEV), semantic=fx_u, label=lx_u)
        x_u_gen_dtch = x_u_gen.detach()

        l1 = l1_loss(d1, x_p, x_p_gen_dtch)
        l2 = l2_loss(d2, x_p, x_p_gen_dtch)
        l3 = l3_loss(d1, x_u, x_u_gen_dtch)
        l4 = l4_loss(d2, x_u, x_u_gen_dtch)

        dl_loss = -((ld1 * l1) + l2 + (ld2 * l3) + l4)
        check_nan(dl_loss.item(), dl1=l1.item(), dl2=l2.item(), dl3=l3.item(), dl4=l4.item())
        dl_loss.backward()

        d1_op.step()
        d2_op.step()

        # GENERATOR TRAINING SECTION
        # print("UPDATING GENERATOR")
        G_op.zero_grad()

        l1 = l1_loss(d1, x_p, x_p_gen, train_gen=True)
        l2 = l2_loss(d2, x_p, x_p_gen, train_gen=True)
        l3 = l3_loss(d1, x_u, x_u_gen, train_gen=True)
        l4 = l4_loss(d2, x_u, x_u_gen, train_gen=True)

        gl_loss = -((ld1 * l1) + l2 + (ld2 * l3) + l4)
        check_nan(gl_loss.item(), gl1=l1.item(), gl2=l2.item(), gl3=l3.item(), gl4=l4.item())
        gl_loss.backward()

        G_op.step()

        # Logging the progress
        batches_done = epch * len(dat_loader) + i
        batches_left = EPCH_END * len(dat_loader) - batches_done
        time_left = datetime.timedelta(seconds=batches_left * (time.time() - prev_time))
        prev_time = time.time()
        # print(j_loss.item(), dl_loss.item())

        if SAMPLE_INTERVAL != -1 and epch % SAMPLE_INTERVAL == 0 and i == 0:
            sample_images(epch)

        sys.stdout.write(
            "\r[Epoch %d/%d] [Batch %d/%d] [J loss: %f 1[%f] 2[%f] 3[%f] 4[%f] 5[%f]] [D loss: %s] [G loss: %s] ETA: %s"
            % (
                epch,
                EPCH_END,
                i,
                len(dat_loader),
                j_loss.item(),
                j1.item(),
                j2.item(),
                j3.item(),
                j4.item(),
                j5.item(),
                dl_loss.item(),
                gl_loss.item(),
                time_left,
            )
        )
        # print(j_loss.item(), dl_loss.item())

        if CHCK_PNT_INTERVAL != -1 and epch % CHCK_PNT_INTERVAL == 0 and i == 0:
            # Save model checkpoints
            torch.save(G.state_dict(), "saved_models/%s/%d_G.pth" % (dataset.get_name(), epch))
            torch.save(d1.state_dict(), "saved_models/%s/%d_d1.pth" % (dataset.get_name(), epch))
            torch.save(d2.state_dict(), "saved_models/%s/%d_d2.pth" % (dataset.get_name(), epch))
            torch.save(sx.state_dict(), "saved_models/%s/%d_sx.pth" % (dataset.get_name(), epch))
            torch.save(sy.state_dict(), "saved_models/%s/%d_sy.pth" % (dataset.get_name(), epch))