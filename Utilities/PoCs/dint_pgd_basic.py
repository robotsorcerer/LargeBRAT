__author__ 		= "Lekan Molu"
__copyright__ 	= "2021, Large Hamilton-Jacobi Analysis."
__license__ 	= "Molux License"
__comment__ 	= "Double Integrator Dynamics Time to reach origin under Maximum Controlled Invariant Subspace."
__maintainer__ 	= "Lekan Molu"
__email__ 		= "patlekno@icloud.com"
__status__ 		= "Working Concept. Stuck on Subspace Identification"


import sys
import copy
import time
import logging
import argparse
import cupy as cp
import numpy as np
import scipy.linalg as la
from os.path import abspath, join, expanduser
import matplotlib.pyplot as plt

sys.path.append(abspath(join('..')))
sys.path.append(abspath(join('../..')))
from LevelSetPy.Utilities        import *
from LevelSetPy.Grids            import createGrid
from LevelSetPy.Helper           import postTimeStepTTR
from LevelSetPy.Visualization    import implicit_mesh
from LevelSetPy.DynamicalSystems import DoubleIntegrator
from BRATVisualization.DIVisu    import DoubleIntegratorVisualizer

# POD Decomposition
from LevelSetPy.POD              import *

# Chambolle-Pock for Total Variation De-Noising
from LevelSetPy.Optimization     import chambollepock

# Value co-state and Lax-Friedrichs upwinding schemes
from LevelSetPy.InitialConditions import *
from LevelSetPy.SpatialDerivative import upwindFirstENO2
from LevelSetPy.ExplicitIntegration import artificialDissipationGLF
from LevelSetPy.ExplicitIntegration.Integration import odeCFL2, odeCFLset
from LevelSetPy.ExplicitIntegration.Term import termRestrictUpdate, termLaxFriedrichs

parser = argparse.ArgumentParser(description='Hamilton-Jacobi Analysis')
parser.add_argument('--silent', '-si', action='store_true', help='silent debug print outs' )
parser.add_argument('--save', '-sv', action='store_true', help='save BRS/BRT at end of sim' )
parser.add_argument('--visualize', '-vz', action='store_false', help='visualize level sets?' )
parser.add_argument('--load_brt', '-lb', action='store_false', help='load saved brt?' )
parser.add_argument('--init_cond', '-ic', type=str, default='circle', help='visualize level sets?' )
parser.add_argument('--elevation', '-el', type=float, default=5., help='elevation angle for target set plot.' )
parser.add_argument('--azimuth', '-az', type=float, default=5., help='azimuth angle for target set plot.' )
parser.add_argument('--pause_time', '-pz', type=float, default=1, help='pause time between successive updates of plots' )
args = parser.parse_args()
args.verbose = True if not args.silent else False

print(f'args:  {args}')

if not args.silent:
	logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)
else:
	logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)

logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
# Turn off pyplot's spurious dumps on screen
logging.getLogger('matplotlib.font_manager').disabled = True
logger = logging.getLogger(__name__)


dint = DoubleIntegrator
u_bound = 1


def preprocessing():
    global dint, u_bound

    gmin = np.array(([[-1, -1]]),dtype=np.float64).T
    gmax = np.array(([[1, 1]]),dtype=np.float64).T
    g = createGrid(gmin, gmax, 101, None)

    eps_targ = 1.0
    target_rad = .2

    dint = DoubleIntegrator(g, u_bound)
    attr = dint.mttr() - target_rad
    if strcmp(args.init_cond, 'attr'):
        value_func_init = attr
    elif strcmp(args.init_cond, 'circle'):
        value_func_init = shapeSphere(g, zeros(g.dim, 1), target_rad)
    elif strcmp(args.init_cond, 'square'):
        value_func_init = shapeRectangleByCenter(g, zeros(g.dim, 1), \
                                    2*target_rad*ones(g.dim,1))

    attr = np.maximum(0, attr)

    return g, attr, value_func_init

def show_init_levels(g, attr, value_func_init):

    f, (ax1, ax2) = plt.subplots(1,2,figsize=(12, 4))

    ax1.contour(g.xs[0], g.xs[1], attr, colors='red')
    ax1.set_title('Analytical TTR')
    ax1.set_xlabel(r"$x_1 (m)$")
    ax1.set_ylabel(r"$x_2 (ms^{-1})$")
    ax1.set_xlim([-1.02, 1.02])
    ax1.set_ylim([-1.01, 1.01])
    ax1.grid()

    ax2.contour(g.xs[0], g.xs[1], value_func_init, colors='blue')
    ax2.set_title('Numerical TTR')
    ax2.set_xlabel(r"$x_1 (m)$")
    ax2.set_xlim([-1.02, 1.02])
    ax2.set_ylim([-1.01, 1.01])
    ax2.grid()

    f.suptitle(f"Levelsets")

    f.canvas.draw()
    f.canvas.flush_events()
    time.sleep(args.pause_time)

def show_trajectories(g, attr, x_i):
	### pick a bunch of initial conditions
	Δ = lambda u: u  # u is either +1 or -1

	# how much timesteps to consider
	t = np.linspace(-1, 1, 100)
	# do implicit euler integration to obtain x1 and x2
	x1p = np.empty((len(x_i), g.xs[0].shape[1], len(t)))
	x2p = np.empty((len(x_i), g.xs[1].shape[1], len(t)))
	# states under negative control law
	x1m  = np.empty((len(x_i), g.xs[0].shape[1], len(t)))
	x2m = np.empty((len(x_i), g.xs[1].shape[1], len(t)))

	fig, ax1 = plt.subplots(1, 1, figsize=(16,9))

	for i in range(len(x_i)):
		for k in range(len(t)):
			x2p[i, :,k] = x_i[i][1] + Δ(u_bound) * t[k]
			x1p[i, :,k] = x_i[i][0] + .5 * Δ(u_bound) * x2p[i,:,k]**2 - .5 * Δ(u_bound) * x_i[i][1]**2
			# state trajos under -ve control law
			x2m[i, :,k] = x_i[i][1] + Δ(-u_bound) * t[k]
			x1m[i, :,k] = x_i[i][0]+.3 + .5 * Δ(-u_bound) * x2p[i,:,k]**2 - .5 * Δ(-u_bound) * x_i[i][1]**2

	fontdict = {'fontsize':28, 'fontweight':'bold'}

	# Plot a few snapshots for different initial conditions.
	color = iter(plt.cm.inferno_r(np.linspace(.25, 1, 2*len(x_i))))
	# repeat for legends
	for init_cond in range(0, len(x_i)):
		# state trajectories are unique for every initial cond
		# here, we pick the last state
		ax1.plot(x1p[init_cond, -1, :], x2p[init_cond, -1, :], linewidth=3, color=next(color), \
			label=rf"x$_{init_cond+1}^+={x_i[init_cond]}$")
		ax1.plot(x1m[init_cond, -1, :], x2m[init_cond, -1, :], '-.', linewidth=3, color=next(color) , \
			label=rf"x$_{init_cond+1}^-={x_i[init_cond]}$")

		#plot the quivers
		ax1.grid('on')
		up, vp = x2p[init_cond, -1, ::len(t)//2], [Δ(u_bound)]*len(x2p[init_cond, -1, ::len(t)//2])
		um, vm = x2m[init_cond, -1, ::len(t)//2], [Δ(-u_bound)]*len(x2m[init_cond, -1, ::len(t)//2])
		ax1.quiver(x1p[init_cond, -1, ::len(t)//2], x2p[init_cond, -1, ::len(t)//2], up, vp, angles='xy')
		ax1.quiver(x1m[init_cond, -1, ::len(t)//2], x2m[init_cond, -1, ::len(t)//2], um, vm, angles='xy')

	ax1.set_ylabel(rf"${{x}}_2$", fontdict=fontdict)
	ax1.set_xlabel(rf"${{x}}_1$", fontdict=fontdict)
	ax1.tick_params(axis='both', which='major', labelsize=28)
	ax1.tick_params(axis='both', which='minor', labelsize=18)
	ax1.set_title(rf"State Trajectories.", fontdict=fontdict)
	ax1.legend(loc="center left", fontsize=8)

	fig.savefig(join(expanduser("~"),"Documents/Papers/Safety/PGDReach", "figures/doub_int_trajos.jpg"),
                bbox_inches='tight',facecolor='None')
	# fig.canvas.draw()
	# fig.canvas.flush_events()
	time.sleep(args.pause_time)

### Plot the switching curve
fontdict = {'fontsize':42, 'fontweight':'bold'}
def show_switch_curve():
	# Plot all vectograms(snapshots) in space and time.
	fig3, ax3 = plt.subplots(1, 1, figsize=(14,13))
	ax3.grid('on')
	color = iter(plt.cm.seismic_r(np.linspace(.25, 1, 4)))
	ax3.plot(dint.Gamma[0,:], linewidth=7.5, color=next(color), label=rf"Switching Curve, $\gamma$")
	xmin, xmax = ax3.get_xlim()
	ymin, ymax = ax3.get_ylim()
	ax3.hlines(0, xmin, xmax, colors='black', linestyles='solid', label='')
	ax3.vlines(len(dint.Gamma)//2, ymin, ymax, colors='black', linestyles='solid', label='')

	ax3.set_xlim(0, 100)
	ax3.set_ylim(-.55, .55)

	ax3.set_xlabel(rf"${{x}}_1$", fontdict=fontdict)
	ax3.set_ylabel(rf"${{x}}_2$", fontdict=fontdict)
	ax3.set_title(rf"Switching Curve, $\gamma$", fontdict=fontdict)
	ax3.tick_params(axis='both', which='major', labelsize=42)
	ax3.tick_params(axis='both', which='minor', labelsize=22)
	ax3.legend(fontsize=28)
	plt.tight_layout()

	# fig.suptitle("The Double Integral Plant.", fontdict=fontdict)
	fig3.savefig(join(expanduser("~"),"Documents/Papers/Safety/PGDReach", "figures/switching_curve.jpg"),
				bbox_inches='tight',facecolor='None')

def show_attr():
    # Plot all vectograms in space and time.
    fig2, ax2 = plt.subplots(1, 1, figsize=(16,9))
    cdata = ax2.pcolormesh(g.xs[0], g.xs[1], attr, shading="nearest", cmap="magma_r")
    plt.colorbar(cdata, ax=ax2, extend="both")
    ax2.set_xlabel(rf"${{x}}_1$", fontdict=fontdict)
    ax2.set_ylabel(rf"${{x}}_2$", fontdict=fontdict)
    ax2.set_title(r"Reach Time", fontdict=fontdict)
    ax2.tick_params(axis='both', which='major', labelsize=28)
    ax2.tick_params(axis='both', which='minor', labelsize=18)

    # fig.suptitle("The Double Integral Plant.", fontdict=fontdict)
    fig2.savefig(join(expanduser("~"),"Documents/Papers/Safety/PGDReach", "figures/attr.jpg"),
                bbox_inches='tight',facecolor='None')

def main(g, attr, value_init):
	global dint, u_bound
	#turn the state space over to the gpu
	g.xs = [cp.asarray(x) for x in g.xs]
	finite_diff_data = Bundle(dict(
				innerFunc = termLaxFriedrichs,
				innerData = Bundle({'grid':g,
					'hamFunc': dint.hamiltonian,
					'partialFunc': dint.dissipation,
					'dissFunc': artificialDissipationGLF,
					'CoStateCalc': upwindFirstENO2,
					}),
					positive = False,  # direction to grow the updated level set
				))

	small = 100*eps
	t_span = np.linspace(0, 2.0, 20)
	options = Bundle(dict(factorCFL=0.75, stats='on', maxStep=realmax, 						singleStep='off', postTimestep=postTimeStepTTR))

	y = copy.copy(value_init.flatten())
	y, finite_diff_data = postTimeStepTTR(0, y, finite_diff_data)
	value_func = cp.asarray(copy.copy(y.reshape(g.shape)))

	U, Sigma, V = la.svd(value_init, full_matrices=False)

	np.allclose(U@np.diag(Sigma)@V, value_init)

	# informed reduced basis rank
	min_rank = minimal_projection_error(value_init, U, eps=1e-5, plot=True)
	print('min_rank ', min_rank)

	Ur, Sigmar, Vr = U[:,:min_rank], Sigma[:min_rank], V[:,:min_rank]
	# be sure the singular values are arranged in descending order

	f = plt.gcf()
	ax = plt.gca()
	ax.grid('on')
	ax.set_title('POD basis vectors $<\epsilon$', fontdict=fontdict)
	ax.set_xlabel(r"Reduced basis rank $r$", fontdict=fontdict)
	ax.set_ylabel(r"Projection error", fontdict=fontdict)
	ax.tick_params(axis='both', which='major', labelsize=42)
	ax.tick_params(axis='both', which='minor', labelsize=22)
	ax.legend(fontsize=28)
	plt.tight_layout()
	f.savefig(join(expanduser("~"),"Documents/Papers/Safety/PGDReach", "figures/proj_error.jpg"),
				bbox_inches='tight',facecolor='None')


	# project value function in reduced basis
	value_rob = Ur.T @ value_init @ Ur
	print(f'Value function in ROB: {value_rob.shape}')


	# calculate associated rhs functions on the reduced model
	gmin = np.array(([[-1, -1]]),dtype=np.float64).T
	gmax = np.array(([[1, 1]]),dtype=np.float64).T
	gr = createGrid(gmin, gmax, np.array(([value_rob.shape])), None)

	dint_rob = DoubleIntegrator(gr)
	# value_rob = dint_rob.mttr()
	reduced_data = Bundle(dict(innerFunc = termLaxFriedrichs,
				innerData = Bundle({'grid':gr,
					'hamFunc': dint_rob.hamiltonian,
					'partialFunc': dint_rob.dissipation,
					'dissFunc': artificialDissipationGLF,
					'CoStateCalc': upwindFirstENO2,
					}),
					positive = False,  # we want to solve a min problem
				))

	y_rob = copy.copy(value_rob.flatten())
	y_rob, reduced_data = postTimeStepTTR(0, y_rob, reduced_data)
	value_rob = cp.asarray(copy.copy(y_rob.reshape(gr.shape)))

	# Visualization paramters
	spacing = tuple(g.dx.flatten().tolist())
	#turn the state space over to the gpu
	g.xs = [cp.asarray(x) for x in g.xs]
	gr.xs = [cp.asarray(x) for x in gr.xs]

	params = Bundle(
						{'grid': g,
						'g_rom': gr,
						'disp': True,
						'labelsize': 16,
						'labels': "Initial 0-LevelSet",
						'linewidth': 2,
						'elevation': args.elevation,
						'azimuth': args.azimuth,
						'mesh': value_func.get(),
						'pgd_mesh': value_rob.get(),
						'init_conditions': False,
						'pause_time': args.pause_time,
						'level': 0, # which level set to visualize
						'winsize': (16,9),
						'fontdict': Bundle({'fontsize':12, 'fontweight':'bold'}),
						"savedict": Bundle({"save": False,
									"savename": "dint_pgd.jpg",
									"savepath": "../jpeg_dumps"})
						})

	if args.visualize:
		viz = DoubleIntegratorVisualizer(params)

	value_func_all = np.zeros((len(t_span),)+value_func.shape)
	value_pgd_all = np.zeros((len(t_span),)+value_rob.shape)

	cur_time, max_time = 0, t_span[-1]
	step_time = (t_span[-1]-t_span[0])/8.0

	start_time = cputime()
	itr_start = cp.cuda.Event()
	itr_end = cp.cuda.Event()

	idx = 0

	while max_time-cur_time > small * max_time:
		itr_start.record()
		cpu_start = cputime()

		time_step = f"{cur_time:.2f}/{max_time:.2f}"

		y0 = value_func.flatten()
		y0_rob = cp.asarray(value_rob.flatten())

		#How far to integrate
		t_span = np.hstack([cur_time, min(max_time, cur_time + step_time)])

		# advance one step of integration
		t, y, finite_diff_data = odeCFL2(termRestrictUpdate, t_span, y0, options, finite_diff_data)
		t_rob, y_rob, finite_diff_rob = odeCFL2(termRestrictUpdate, t_span, y0_rob, options, reduced_data)
		cp.cuda.Stream.null.synchronize()
		cur_time = t if np.isscalar(t) else t[-1]

		value_func = cp.reshape(y, g.shape)
		value_rob = cp.reshape(y_rob, gr.shape)

		if args.visualize:
			ls_mesh = value_func.get()
			pgd_mesh = value_rob.get()
			viz.update_tube(attr, ls_mesh, pgd_mesh, cur_time, delete_last_plot=True)

		itr_end.record()
		itr_end.synchronize()
		cpu_end = cputime()

		print(f't: {time_step} | GPU time: {(cp.cuda.get_elapsed_time(itr_start, itr_end)):.2f} | CPU Time: {(cpu_end-cpu_start):.2f}')
		print(f'Bnds {min(y):.2f}/{max(y):.2f} | PGD Bnds {min(y_rob):.2f}/{max(y_rob):.2f} | Norm: {np.linalg.norm(y, 2):.2f} | PGD Norm: {np.linalg.norm(y_rob, 2):.2f} ')

		# store this brt
		value_func_all[idx] = ls_mesh
		value_pgd_all[idx]  = pgd_mesh

		idx += 1

	end_time = cputime()
	print(f'Total BRS/BRT execution time {(end_time - start_time):.4f} seconds.')


if __name__ == '__main__':
	g, attr, value_init = preprocessing()
	print("Showing analytical time to reach.")

	if args.visualize:
		plt.ion()

		print("Showing initial level sets.")
		show_init_levels(g, attr, value_init)

		print('\n\nShowing Trajectories.')
		xis = [(1,0), (.25, 0), (.5, 0), (.75, 0),  (0,0), (-.25, 0), (-.5, 0), (.75, 0), (-1,0)]
		xis = [(1,0), (.5, 0),  (0,0), (-.5, 0), (-1,0)]
		show_trajectories(g, attr, xis)

		print('\n\nShowing Switch Curve.')
		show_switch_curve()

		print('\n\nShowing Analytical Time to Reach.')
		show_attr()

		time.sleep(4)
		plt.ioff()

	plt.ion()

	print("\n\nEvolving the level set.")
	main(g, attr, value_init)
	plt.ioff()
	plt.show()
