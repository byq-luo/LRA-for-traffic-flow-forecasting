#encoding:utf-8
from __future__ import division
from __future__ import print_function
from inference.utils import get_batch,prepare_feed_data
import tensorflow as tf

from ae.flags import FLAGS
from inference.config import *

class AutoEncoder(object):
  """Generic deep autoencoder.

  Autoencoder used for full training cycle, including
  unsupervised pretraining layers and final fine tuning.
  The user specifies the structure of the neural net
  by specifying number of inputs, the number of hidden
  units for each layer and the number of final output
  logits.
  """
  _weights_str = "weights{0}"
  _biases_str = "biases{0}"

  def __init__(self, shape, sess):
    """Autoencoder initializer

    Args:
      shape: list of ints specifying
              num input, hidden1 units,...hidden_n units, num logits
      sess: tensorflow session object to use
    """
    self.__shape = shape  # [input_dim,hidden1_dim,...,hidden_n_dim,output_dim]
    self.__num_hidden_layers = len(self.__shape) - 2

    self.__variables = {}
    self.__sess = sess

    self._setup_variables()

  @property
  def shape(self):
    return self.__shape

  @property
  def num_hidden_layers(self):
    return self.__num_hidden_layers

  @property
  def session(self):
    return self.__sess

  def __getitem__(self, item):
    """Get autoencoder tf variable

    Returns the specified variable created by this object.
    Names are weights#, biases#, biases#_out, weights#_fixed,
    biases#_fixed.

    Args:
     item: string, variables internal name
    Returns:
     Tensorflow variable
    """
    return self.__variables[item]

  def __setitem__(self, key, value):
    """Store a tensorflow variable

    NOTE: Don't call this explicity. It should
    be used only internally when setting up
    variables.

    Args:
      key: string, name of variable
      value: tensorflow variable
    """
    self.__variables[key] = value

  def _setup_variables(self):
    with tf.name_scope("autoencoder_variables"):
      for i in range(self.__num_hidden_layers + 1):
        # Train weights
        name_w = self._weights_str.format(i + 1)
        w_shape = (self.__shape[i], self.__shape[i + 1])
        a = tf.multiply(4.0, tf.sqrt(6.0 / (w_shape[0] + w_shape[1])))
        w_init = tf.random_uniform(w_shape, -1 * a, a)
        # w_init = tf.truncated_normal(w_shape,stddev=0.1)
        self[name_w] = tf.Variable(w_init,
                                   name=name_w,
                                   trainable=True,dtype=tf.float32)
        # Train biases
        name_b = self._biases_str.format(i + 1)
        b_shape = (self.__shape[i + 1],)
        b_init = tf.zeros(b_shape)
        self[name_b] = tf.Variable(b_init, trainable=True, name=name_b,dtype=tf.float32)

        if i < self.__num_hidden_layers:
          # Hidden layer fixed weights (after pretraining before fine tuning)
          self[name_w + "_fixed"] = tf.Variable(tf.identity(self[name_w]),
                                                name=name_w + "_fixed",
                                                trainable=False,dtype=tf.float32)

          # Hidden layer fixed biases
          self[name_b + "_fixed"] = tf.Variable(tf.identity(self[name_b]),
                                                name=name_b + "_fixed",
                                                trainable=False,dtype=tf.float32)

          # Pretraining output training biases
          name_b_out = self._biases_str.format(i + 1) + "_out"
          b_shape = (self.__shape[i],)
          b_init = tf.zeros(b_shape)
          self[name_b_out] = tf.Variable(b_init,
                                         trainable=True,
                                         name=name_b_out)

  def _w(self, n, suffix=""):
    return self[self._weights_str.format(n) + suffix]

  def _b(self, n, suffix=""):
    return self[self._biases_str.format(n) + suffix]

  def get_variables_to_init(self, n):
    """Return variables that need initialization

    This method aides in the initialization of variables
    before training begins at step n. The returned
    list should be than used as the input to
    tf.initialize_variables

    Args:
      n: int giving step of training
    """
    assert n > 0
    assert n <= self.__num_hidden_layers + 1

    vars_to_init = [self._w(n), self._b(n)]

    if n <= self.__num_hidden_layers:
      vars_to_init.append(self._b(n, "_out"))

    if 1 < n <= self.__num_hidden_layers:
      vars_to_init.append(self._w(n - 1, "_fixed"))
      vars_to_init.append(self._b(n - 1, "_fixed"))

    return vars_to_init

  @staticmethod
  def _activate(x, w, b, transpose_w=False):
    y = tf.sigmoid(tf.nn.bias_add(tf.matmul(x, w, transpose_b=transpose_w), b))
    return y

  def pretrain_net(self, input_pl, n, is_target=False):
    """Return net for step n training or target net

    Args:
      input_pl:  tensorflow placeholder of AE inputs
      n:         int specifying pretrain step
      is_target: bool specifying if required tensor
                  should be the target tensor
    Returns:
      Tensor giving pretraining net or pretraining target
    """
    assert n > 0
    assert n <= self.__num_hidden_layers

    last_output = input_pl
    for i in range(n - 1):
      w = self._w(i + 1, "_fixed")
      b = self._b(i + 1, "_fixed")

      last_output = self._activate(last_output, w, b)

    if is_target:
      return last_output

    last_output = self._activate(last_output, self._w(n), self._b(n))

    out = self._activate(last_output, self._w(n), self._b(n, "_out"),
                         transpose_w=True)
    out = tf.maximum(out, 1.e-9)
    out = tf.minimum(out, 1 - 1.e-9)
    return out

  def supervised_net(self, input_pl,regularizer):
    """Get the supervised fine tuning net

    Args:
      input_pl: tf placeholder for ae input data
    Returns:
      Tensor giving full ae net
    """
    last_output = input_pl

    for i in range(self.__num_hidden_layers + 1):
      # Fine tuning will be done on these variables
      w = self._w(i + 1)
      regularizer(w)
      b = self._b(i + 1)

      last_output = self._activate(last_output, w, b)

    return last_output



def training(loss, learning_rate, loss_key=None):
  """Sets up the training Ops.

  Creates a summarizer to track the loss over time in TensorBoard.

  Creates an optimizer and applies the gradients to all trainable variables.

  The Op returned by this function is what must be passed to the
  `sess.run()` call to cause the model to train.

  Args:
    loss: Loss tensor, from loss().
    learning_rate: The learning rate to use for gradient descent.
    loss_key: int giving stage of pretraining so we can store
                loss summaries for each pretraining stage

  Returns:
    train_op: The Op for training.
  """
  # Create the gradient descent optimizer with the given learning rate.
  # optimizer = tf.train.GradientDescentOptimizer(learning_rate)
  optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate)
  # Create a variable to track the global step.
  global_step = tf.Variable(0, name='global_step', trainable=False)
  # Use the optimizer to apply the gradients that minimize the loss
  # (and also increment the global step counter) as a single training step.
  train_op = optimizer.minimize(loss, global_step=global_step)
  return train_op, global_step

def loss_x_entropy(output, target):
  """Cross entropy loss

  See https://en.wikipedia.org/wiki/Cross_entropy

  Args:
    output: tensor of net output
    target: tensor of net we are trying to reconstruct
  Returns:
    Scalar tensor of cross entropy
  """
  with tf.name_scope("xentropy_loss"):
      net_output_tf = tf.convert_to_tensor(output, name='input')
      target_tf = tf.convert_to_tensor(target, name='target')
      cross_entropy = tf.add(tf.multiply(tf.log(net_output_tf, name='log_output'),
                                    target_tf),
                             tf.multiply(tf.log(1 - net_output_tf),
                                    (1 - target_tf)))
      return -1 * tf.reduce_mean(tf.reduce_sum(cross_entropy, 1),
                                 name='xentropy_mean')

def main_unsupervised(sess,get_data_factors,place_holder_set,res_data,input_layer_num):
    num_hidden = FLAGS.num_hidden_layers
    ae_hidden_shapes = [getattr(FLAGS, "hidden{0}_units".format(j + 1))
                        for j in range(num_hidden)]
    ae_shape = [input_layer_num] + ae_hidden_shapes + [FLAGS.num_classes]

    ae = AutoEncoder(ae_shape, sess)
    num_train = TRAIN_SAMPLE_NUMS_FIFTEEN // BATCH_SIZE

    learning_rates = {j: getattr(FLAGS,
                                 "pre_layer{0}_learning_rate".format(j + 1))
                      for j in range(num_hidden)}

    noise = {j: getattr(FLAGS, "noise_{0}".format(j + 1))
             for j in range(num_hidden)}

    for i in range(len(ae_shape) - 2):
      n = i + 1
      with tf.variable_scope("pretrain_{0}".format(n)):
        input_ = res_data
        target_ = res_data
        layer = ae.pretrain_net(input_, n)

        with tf.name_scope("target"):
          target_for_loss = ae.pretrain_net(target_, n, is_target=True)

        # loss = loss_x_entropy(layer, target_for_loss)
        # self denifition loss
        loss = tf.reduce_mean(tf.square(layer - target_for_loss))

        temp = set(tf.global_variables())
        train_op, global_step = training(loss, learning_rates[i], i)
        op_varible = set(tf.global_variables()) - temp


        vars_to_init = ae.get_variables_to_init(n)
        vars_to_init = vars_to_init + list(op_varible)
        sess.run(tf.variables_initializer(vars_to_init))
        print("\n\n")
        print("| Training Step | Cross Entropy |  Layer  |   Iteration  |")
        print("|---------------|---------------|---------|--------------|")
        # FLAGS.pretraining_epochs * num_train


        for step in range(FLAGS.pretraining_epochs * num_train):
          batch_data= get_batch(sess,get_data_factors[0],get_data_factors[1],get_data_factors[2])
          date, traffic_input, targets = prepare_feed_data(batch_data)
          _,loss_value = sess.run([train_op, loss],feed_dict={place_holder_set[0]:traffic_input,place_holder_set[1]:targets,place_holder_set[2]:True})

          if step % 1000 == 0:
            output = "| {0:>13} | {1:13.4f} | Layer {2} | Epoch {3}  |"\
                     .format(step, loss_value, n, step)
            print(output)

        # thread interrupt
        # tf.assign(ae.__getitem__(ae._weights_str.format(n)+"_fixed"),ae.__getitem__(ae._weights_str.format(n)))
        # tf.assign(ae.__getitem__(ae._biases_str.format(n) + "_fixed"), ae.__getitem__(ae._biases_str.format(n)))

    return ae


def main_supervised(ae,inputs,regularizer):
    # for i in range(len(ae.shape)-2):
    #     n = i + 1
    #     tf.assign(ae.__getitem__(ae._weights_str.format(n)),ae.__getitem__(ae._weights_str.format(n) + "_fixed"))
    #     tf.assign(ae.__getitem__(ae._biases_str.format(n)),ae.__getitem__(ae._biases_str.format(n) + "_fixed"))

    logits = ae.supervised_net(inputs,regularizer)
    mat = tf.reshape(logits,[-1,INPUT_SIZE,INPUT_SIZE])
    # mat = tf.reshape(logits, [-1, INPUT_SIZE])

    return mat

